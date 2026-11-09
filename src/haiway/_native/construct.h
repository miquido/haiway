/* ---------------------------------------------------------- construction */

static PyObject *
struct_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    /* only the storage is allocated here - binding the values is what __init__ does,
     * which is what lets a class declare an __init__ of its own and delegate to it */
    if (plan_or_error(type) == NULL) return NULL;
    return type->tp_alloc(type, 0);
}

/*
 * The value given for an attribute, by its name or by its alias - a new reference, NULL
 * when it was not given. Both at once is ambiguous, which is an error.
 */
static PyObject *
given_value(PyObject *kwargs, FieldPlan *field, Py_ssize_t *consumed, CompiledPlan *plan, PyTypeObject *type, int *error)
{
    PyObject *value = NULL;
    if (field->alias != NULL) {
        int r = PyDict_GetItemRef(kwargs, field->alias, &value);
        if (r < 0) {
            *error = 1;
            return NULL;
        }
        if (r > 0) {
            (*consumed)++;
            int both = PyDict_Contains(kwargs, field->name);
            if (both < 0 || (both > 0 && plan->reporting)) {
                Py_DECREF(value);
                if (both > 0) raise_key_error(plan->state, type, kwargs);
                *error = 1;
                return NULL;
            }
            return value;
        }
    }
    int r = PyDict_GetItemRef(kwargs, field->name, &value);
    if (r < 0) {
        *error = 1;
        return NULL;
    }
    if (r > 0) (*consumed)++;
    return value;
}

/* the values of every attribute, validated, in `values` - 0 with an error set on failure */
static int
bind_values(CompiledPlan *plan, PyTypeObject *type, PyObject *kwargs, PyObject **values)
{
    Py_ssize_t n = plan->count;
    Py_ssize_t consumed = 0;
    int error = 0;
    for (Py_ssize_t i = 0; i < n; i++) values[i] = NULL;

    for (Py_ssize_t i = 0; i < n; i++) {
        FieldPlan *field = &plan->fields[i];
        PyObject *value = kwargs == NULL ? NULL : given_value(kwargs, field, &consumed, plan, type, &error);
        if (error) goto fail;

        PyObject *validated;
        if (value != NULL) {
            validated = validate_field(field, plan, value);
            Py_DECREF(value);
        } else {
            validated = field_default(field);
        }
        if (validated == NULL) {
            report_failure(plan->state, type, kwargs, field->name);
            goto fail;
        }
        values[i] = validated;
    }

    if (plan->reporting && kwargs != NULL && consumed != PyDict_GET_SIZE(kwargs)) {
        raise_key_error(plan->state, type, kwargs);
        goto fail;
    }
    return 1;

fail:
    for (Py_ssize_t i = 0; i < n; i++) Py_CLEAR(values[i]);
    return 0;
}

/* whether any value is bound already - a plain record can unbind some of its values
 * (`object.__delattr__`), which must not make it initializable again */
static inline int
any_bound(PyObject **items, Py_ssize_t n)
{
    for (Py_ssize_t i = 0; i < n; i++) {
        if (items[i] != NULL) return 1;
    }
    return 0;
}

static int
struct_init(PyObject *obj, PyObject *args, PyObject *kwargs)
{
    PyTypeObject *type = Py_TYPE(obj);
    if (PyTuple_GET_SIZE(args) != 0) {
        PyErr_Format(PyExc_TypeError, "%s takes keyword arguments only", type->tp_name);
        return -1;
    }
    CompiledPlan *plan = plan_or_error(type);
    if (plan == NULL) return -1;
    Py_ssize_t n = plan->count;
    PyObject **items = ITEMS(obj);
    if (any_bound(items, n)) {
        PyErr_Format(PyExc_TypeError, "%s is initialized already and immutable", type->tp_name);
        return -1;
    }

    /* the values are validated first, with nothing bound - and bound all at once */
    PyObject *stack[FAST_MAX_FIELDS];
    PyObject **values = n <= FAST_MAX_FIELDS ? stack : PyMem_Malloc((size_t)n * sizeof(PyObject *));
    if (values == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    int bound = 0;
    if (bind_values(plan, type, kwargs, values)) {
        Py_BEGIN_CRITICAL_SECTION(obj);
        if (!any_bound(items, n)) {
            for (Py_ssize_t i = 0; i < n; i++) items[i] = values[i];
            bound = 1;
        }
        Py_END_CRITICAL_SECTION();
        if (!bound) {
            for (Py_ssize_t i = 0; i < n; i++) Py_DECREF(values[i]);
            PyErr_Format(PyExc_TypeError, "%s is initialized already and immutable", type->tp_name);
        }
    }
    if (values != stack) PyMem_Free(values);
    if (!bound) return -1;
    finish_construction(obj);
    return 0;
}

/* a new instance of the same class, with the values it is given */
static PyObject *
struct_replace(PyObject *self, PyObject *args, PyObject *kwargs)
{
    if (PyTuple_GET_SIZE(args) != 0) {
        PyErr_SetString(PyExc_TypeError, "__replace__ takes keyword arguments only");
        return NULL;
    }
    if (kwargs == NULL || PyDict_GET_SIZE(kwargs) == 0) return Py_NewRef(self);

    PyTypeObject *type = Py_TYPE(self);
    CompiledPlan *plan = plan_or_error(type);
    if (plan == NULL) return NULL;

    Py_ssize_t n = plan->count;
    PyObject *copy = type->tp_alloc(type, 0);
    if (copy == NULL) return NULL;
    PyObject **source = ITEMS(self);
    PyObject **target = ITEMS(copy);
    Py_ssize_t consumed = 0;
    int error = 0;

    for (Py_ssize_t i = 0; i < n; i++) {
        FieldPlan *field = &plan->fields[i];
        PyObject *value = given_value(kwargs, field, &consumed, plan, type, &error);
        if (error) goto fail;

        if (value == NULL) {
            /* an absent key carries the attribute over - a MISSING value clears it */
            target[i] = Py_XNewRef(source[i]);
            continue;
        }

        PyObject *validated = validate_field(field, plan, value);
        Py_DECREF(value);
        if (validated == NULL) {
            report_failure(plan->state, type, kwargs, field->name);
            goto fail;
        }
        target[i] = validated;
    }

    if (plan->reporting && consumed != PyDict_GET_SIZE(kwargs)) {
        raise_key_error(plan->state, type, kwargs);
        goto fail;
    }
    finish_update(copy, self);
    return copy;

fail:
    Py_DECREF(copy);
    return NULL;
}

/*
 * Which keyword argument each attribute was given in, -1 for none - decided before any
 * value is looked at. 0 when the call is not one this handles: a keyword which is not
 * an attribute (or not spelled with the very string of its name), or one attribute
 * given twice. The keywords usually come in the order of the attributes, so that is
 * where each is looked for first.
 */
static int
match_keywords(CompiledPlan *plan, PyObject *kwnames, Py_ssize_t nkw, int *given)
{
    Py_ssize_t count = plan->count;
    for (Py_ssize_t i = 0; i < count; i++) given[i] = -1;
    for (Py_ssize_t k = 0; k < nkw; k++) {
        PyObject *key = PyTuple_GET_ITEM(kwnames, k);
        Py_ssize_t i = k;
        if (i >= count || (key != plan->fields[i].name && key != plan->fields[i].alias)) {
            for (i = 0; i < count; i++) {
                FieldPlan *field = &plan->fields[i];
                if (key == field->name || key == field->alias) break;
            }
            if (i == count) return 0;
        }
        if (given[i] >= 0) return 0;
        given[i] = (int)k;
    }
    return 1;
}

/* the keyword arguments as the dictionary the general path reads */
static PyObject *
keywords_dict(PyObject *const *values, PyObject *kwnames, Py_ssize_t nkw)
{
    if (nkw == 0) return NULL;
    PyObject *dict = PyDict_New();
    if (dict == NULL) return NULL;
    for (Py_ssize_t k = 0; k < nkw; k++) {
        if (PyDict_SetItem(dict, PyTuple_GET_ITEM(kwnames, k), values[k]) < 0) {
            Py_DECREF(dict);
            return NULL;
        }
    }
    return dict;
}

static PyObject *
struct_vectorcall(PyObject *callable, PyObject *const *args, size_t nargsf, PyObject *kwnames)
{
    PyTypeObject *type = (PyTypeObject *)callable;
    Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
    Py_ssize_t nkw = kwnames == NULL ? 0 : PyTuple_GET_SIZE(kwnames);
    CompiledPlan *plan = nkw <= FAST_MAX_FIELDS && nargs == 0 ? PLAN_OF(type) : NULL;
    int given[FAST_MAX_FIELDS];

    if (plan == NULL || plan->count > FAST_MAX_FIELDS || !match_keywords(plan, kwnames, nkw, given)) {
        /* the general path - the one which reports what is wrong with the call */
        PyObject *positional = PyTuple_New(nargs);
        if (positional == NULL) return NULL;
        for (Py_ssize_t i = 0; i < nargs; i++) PyTuple_SET_ITEM(positional, i, Py_NewRef(args[i]));
        PyObject *kwargs = keywords_dict(args + nargs, kwnames, nkw);
        if (kwargs == NULL && PyErr_Occurred()) {
            Py_DECREF(positional);
            return NULL;
        }
        PyObject *self = struct_new(type, positional, kwargs);
        if (self != NULL && struct_init(self, positional, kwargs) < 0) Py_CLEAR(self);
        Py_DECREF(positional);
        Py_XDECREF(kwargs);
        return self;
    }

    PyObject *self = type->tp_alloc(type, 0);
    if (self == NULL) return NULL;
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < plan->count; i++) {
        FieldPlan *field = &plan->fields[i];
        PyObject *validated = given[i] >= 0 ? validate_field(field, plan, args[nargs + given[i]]) : field_default(field);
        if (validated == NULL) {
            report_failure(plan->state, type, NULL, field->name);
            Py_DECREF(self);
            return NULL;
        }
        items[i] = validated;
    }
    finish_construction(self);
    return self;
}

/*
 * An instance built from the items of a mapping. A key which is not an attribute is
 * reported first, before any of the values is validated. A dictionary is read as it is,
 * locked while its keys are walked - any other mapping is copied into one first.
 */
static PyObject *
instance_from_mapping(ModuleState *state, PyObject *cls, PyObject *mapping)
{
    PyTypeObject *type = (PyTypeObject *)cls;
    CompiledPlan *plan = PLAN_OF(type);

    PyObject *kwargs;
    if (PyDict_CheckExact(mapping)) {
        kwargs = Py_NewRef(mapping);
    } else {  /* a frozendict too - the dictionary calls below read a dictionary alone */
        kwargs = PyDict_New();
        if (kwargs == NULL || PyDict_Update(kwargs, mapping) < 0) {
            Py_XDECREF(kwargs);
            return NULL;
        }
    }

    if (plan != NULL) {
        PyObject *unknown = NULL;  /* the first key which is not an attribute */
        int failed = 0;
        PyObject *key;
        PyObject *value;
        Py_ssize_t position = 0;
        Py_BEGIN_CRITICAL_SECTION(kwargs);
        while (PyDict_Next(kwargs, &position, &key, &value)) {
            PyObject *index;
            int known = PyDict_GetItemRef(plan->keys, key, &index);
            if (known < 0) {
                failed = 1;
                break;
            }
            if (!known) {
                unknown = Py_NewRef(key);
                break;
            }
            Py_DECREF(index);
        }
        Py_END_CRITICAL_SECTION();
        if (failed) {
            Py_DECREF(kwargs);
            return NULL;
        }
        if (unknown != NULL) {
            PyObject *r = PyObject_CallFunctionObjArgs(state->unexpected, cls, unknown, NULL);
            Py_XDECREF(r);
            Py_DECREF(unknown);
            Py_DECREF(kwargs);
            return NULL;
        }
    }

    PyObject *empty = PyTuple_New(0);
    PyObject *self = NULL;
    if (empty == NULL) {
        /* nothing to build with */
    } else if (type->tp_new != struct_new || type->tp_init != struct_init) {
        /* the class takes over its construction - what it makes of the keys is its own */
        self = PyObject_Call(cls, empty, kwargs);
    } else {
        self = struct_new(type, empty, kwargs);
        if (self != NULL && struct_init(self, empty, kwargs) < 0) Py_CLEAR(self);
    }
    Py_XDECREF(empty);
    Py_DECREF(kwargs);
    return self;
}

/* the instance of a class as it is, an instance built from a mapping, or an error */
static PyObject *
struct_validate_value(ModuleState *state, PyObject *cls, PyObject *value)
{
    int matching = PyObject_IsInstance(value, cls);
    if (matching < 0) return NULL;
    if (matching) return Py_NewRef(value);

    int mapping = PyAnyDict_Check(value) ? 1 : PyObject_IsInstance(value, state->mapping);
    if (mapping < 0) return NULL;
    if (mapping) return instance_from_mapping(state, cls, value);

    return type_error_of(cls, value);
}

/* validate(value) - the classmethod of it */
static PyObject *
struct_validate(PyObject *cls, PyObject *value)
{
    CompiledPlan *plan = plan_or_error((PyTypeObject *)cls);
    if (plan == NULL) return NULL;
    return struct_validate_value(plan->state, cls, value);
}

/* __replace__(**values) without a dictionary of the values for the usual call */
static PyObject *
struct_replace_fast(PyObject *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    Py_ssize_t nkw = kwnames == NULL ? 0 : PyTuple_GET_SIZE(kwnames);
    if (nargs == 0 && nkw == 0) return Py_NewRef(self);

    PyTypeObject *type = Py_TYPE(self);
    CompiledPlan *plan = nkw <= FAST_MAX_FIELDS && nargs == 0 ? PLAN_OF(type) : NULL;
    int given[FAST_MAX_FIELDS];
    if (plan == NULL || plan->count > FAST_MAX_FIELDS || !match_keywords(plan, kwnames, nkw, given)) {
        PyObject *positional = PyTuple_New(nargs);
        if (positional == NULL) return NULL;
        for (Py_ssize_t i = 0; i < nargs; i++) PyTuple_SET_ITEM(positional, i, Py_NewRef(args[i]));
        PyObject *kwargs = keywords_dict(args + nargs, kwnames, nkw);
        if (kwargs == NULL && PyErr_Occurred()) {
            Py_DECREF(positional);
            return NULL;
        }
        PyObject *result = struct_replace(self, positional, kwargs);
        Py_DECREF(positional);
        Py_XDECREF(kwargs);
        return result;
    }

    PyObject *copy = type->tp_alloc(type, 0);
    if (copy == NULL) return NULL;
    PyObject **source = ITEMS(self);
    PyObject **target = ITEMS(copy);
    for (Py_ssize_t i = 0; i < plan->count; i++) {
        FieldPlan *field = &plan->fields[i];
        if (given[i] < 0) {
            /* an absent key carries the attribute over - a MISSING value clears it */
            target[i] = Py_XNewRef(source[i]);
            continue;
        }
        PyObject *validated = validate_field(field, plan, args[nargs + given[i]]);
        if (validated == NULL) {
            report_failure(plan->state, type, NULL, field->name);
            Py_DECREF(copy);
            return NULL;
        }
        target[i] = validated;
    }
    finish_update(copy, self);
    return copy;
}

/* values which are their own deep copy - what `copy` treats as atomic, and what can't change */
static inline int
is_atomic(ModuleState *state, PyObject *value)
{
    return value == Py_None || value == state->missing || PyUnicode_CheckExact(value) || PyLong_CheckExact(value)
        || PyFloat_CheckExact(value) || PyBool_Check(value) || PyBytes_CheckExact(value)
        || PyFrozenSet_CheckExact(value) || Py_IS_TYPE(value, (PyTypeObject *)state->map)
        || Py_IS_TYPE(value, (PyTypeObject *)state->meta) || PyType_Check(value)
        || Py_IS_TYPE(value, (PyTypeObject *)state->uuid) || Py_IS_TYPE(value, (PyTypeObject *)state->datetime)
        || Py_IS_TYPE(value, (PyTypeObject *)state->date) || Py_IS_TYPE(value, (PyTypeObject *)state->time)
        || Py_IS_TYPE(value, (PyTypeObject *)state->timedelta);
}

static PyObject *deep_copy(ModuleState *state, PyObject *value, PyObject *memo);
static PyObject *struct_deep_copy(ModuleState *state, PyObject *self, PyObject *memo);

/* what the memo holds for a value, a new reference - NULL without an error for nothing */
static PyObject *
memo_lookup(PyObject *memo, PyObject *value)
{
    PyObject *key = PyLong_FromVoidPtr(value);
    if (key == NULL) return NULL;
    PyObject *found;
    int r = PyDict_GetItemRef(memo, key, &found);
    Py_DECREF(key);
    return r > 0 ? found : NULL;
}

static int
memo_store(PyObject *memo, PyObject *value, PyObject *copy)
{
    PyObject *key = PyLong_FromVoidPtr(value);
    if (key == NULL) return -1;
    int r = PyDict_SetItem(memo, key, copy);
    Py_DECREF(key);
    return r;
}

/* the deep copy of a tuple, the way `copy` makes it - the tuple itself when nothing in it changes */
static PyObject *
tuple_deep_copy(ModuleState *state, PyObject *value, PyObject *memo)
{
    Py_ssize_t n = PyTuple_GET_SIZE(value);
    PyObject *copied = PyTuple_New(n);
    if (copied == NULL) return NULL;
    int same = 1;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *element = PyTuple_GET_ITEM(value, i);
        PyObject *element_copy = deep_copy(state, element, memo);
        if (element_copy == NULL) {
            Py_DECREF(copied);
            return NULL;
        }
        if (element_copy != element) same = 0;
        PyTuple_SET_ITEM(copied, i, element_copy);
    }
    /* copying the elements may have entered the tuple itself, through one referring back to it */
    PyObject *entered = memo_lookup(memo, value);
    if (entered != NULL) {
        Py_DECREF(copied);
        return entered;
    }
    if (PyErr_Occurred()) {
        Py_DECREF(copied);
        return NULL;
    }
    if (same) {
        Py_DECREF(copied);
        return Py_NewRef(value);
    }
    if (memo_store(memo, value, copied) < 0) {
        Py_DECREF(copied);
        return NULL;
    }
    return copied;
}

/* the deep copy of a value - natively for what is atomic, a tuple or an instance, through
 * `copy.deepcopy` for anything else */
static PyObject *
deep_copy(ModuleState *state, PyObject *value, PyObject *memo)
{
    if (is_atomic(state, value)) return Py_NewRef(value);
    if (PyTuple_CheckExact(value) || is_struct(state, value)) {
        PyObject *found = memo_lookup(memo, value);
        if (found != NULL) return found;
        if (PyErr_Occurred()) return NULL;
        if (Py_EnterRecursiveCall(" while copying a State")) return NULL;
        PyObject *copied = PyTuple_CheckExact(value) ? tuple_deep_copy(state, value, memo)
                                                     : struct_deep_copy(state, value, memo);
        Py_LeaveRecursiveCall();
        return copied;
    }
    return PyObject_CallFunctionObjArgs(state->deepcopy, value, memo, NULL);
}

/*
 * The values are copied first, and the copy built from them only then - nothing ever sees
 * an instance with some of its values missing, which the code copying them could keep.
 * A value referring back to the instance copies it while its own copy is made, the way a
 * tuple is copied: the copy made there, complete, is entered into the memo, and is the
 * one returned.
 */
static PyObject *
struct_deep_copy(ModuleState *state, PyObject *self, PyObject *memo)
{
    PyTypeObject *type = Py_TYPE(self);
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject *stack[FAST_MAX_FIELDS];
    PyObject **values = n <= FAST_MAX_FIELDS ? stack : PyMem_Malloc((size_t)n * sizeof(PyObject *));
    if (values == NULL) return PyErr_NoMemory();
    PyObject *result = NULL;
    Py_ssize_t copied = 0;
    for (; copied < n; copied++) {
        PyObject *value = ITEMS(self)[copied];
        values[copied] = value == NULL ? NULL : deep_copy(state, value, memo);
        if (value != NULL && values[copied] == NULL) goto done;
    }

    result = memo_lookup(memo, self);  /* entered while its values were copied */
    if (result != NULL || PyErr_Occurred()) goto done;
    result = type->tp_alloc(type, 0);
    if (result == NULL) goto done;
    for (Py_ssize_t i = 0; i < n; i++) ITEMS(result)[i] = values[i];
    copied = 0;  /* the copy holds them now */
    finish_construction(result);
    if (memo_store(memo, self, result) < 0) Py_CLEAR(result);

done:
    for (Py_ssize_t i = 0; i < copied; i++) Py_XDECREF(values[i]);
    if (values != stack) PyMem_Free(values);
    return result;
}

/* __deepcopy__(memo) */
static PyObject *
struct_deepcopy(PyObject *self, PyObject *memo)
{
    CompiledPlan *plan = plan_or_error(Py_TYPE(self));
    if (plan == NULL) return NULL;
    PyObject *own_memo = NULL;
    if (memo == Py_None) {
        own_memo = PyDict_New();
        if (own_memo == NULL) return NULL;
        memo = own_memo;
    } else if (!PyDict_Check(memo)) {
        PyErr_SetString(PyExc_TypeError, "__deepcopy__ expects a memo dictionary");
        return NULL;
    }
    PyObject *copy = struct_deep_copy(plan->state, self, memo);
    Py_XDECREF(own_memo);
    return copy;
}
