/* ---------------------------------------------------------- construction */

static PyObject *
struct_new(PyTypeObject *type, PyObject *args, PyObject *kwargs)
{
    /* only the storage is allocated here - binding the values is what __init__ does,
     * which is what lets a class declare an __init__ of its own and delegate to it */
    CompiledPlan *plan = plan_or_error(type);
    if (plan == NULL) return NULL;
    return type->tp_alloc(type, plan->count);
}

/* the value given for an attribute, by its name or by its alias - NULL when it was not given */
static inline PyObject *
given_value(PyObject *kwargs, FieldPlan *field, Py_ssize_t *consumed, PyTypeObject *type, int *error)
{
    PyObject *value = NULL;
    if (field->alias != NULL) {
        value = lookup(kwargs, field->alias, error);
        if (*error) return NULL;
        if (value != NULL) {
            (*consumed)++;
            /* the same attribute under both of its keys is ambiguous */
            if (lookup(kwargs, field->name, error) != NULL) {
                raise_key_error(type, kwargs);
                *error = 1;
                return NULL;
            }
            if (*error) return NULL;
        }
    }
    if (value == NULL) {
        value = lookup(kwargs, field->name, error);
        if (*error) return NULL;
        if (value != NULL) (*consumed)++;
    }
    return value;
}

static int
struct_init(PyObject *obj, PyObject *args, PyObject *kwargs)
{
    StructObject *self = (StructObject *)obj;
    PyTypeObject *type = Py_TYPE(obj);
    if (PyTuple_GET_SIZE(args) != 0) {
        PyErr_Format(PyExc_TypeError, "%s takes keyword arguments only", type->tp_name);
        return -1;
    }
    CompiledPlan *plan = plan_or_error(type);
    if (plan == NULL) return -1;

    Py_ssize_t n = plan->count < Py_SIZE(self) ? plan->count : Py_SIZE(self);
    PyObject **items = ITEMS(self);
    if (n > 0 && items[0] != NULL) {
        PyErr_Format(PyExc_TypeError, "%s is initialized already and immutable", type->tp_name);
        return -1;
    }
    Py_ssize_t consumed = 0;
    int error = 0;

    for (Py_ssize_t i = 0; i < n; i++) {
        FieldPlan *field = &plan->fields[i];
        PyObject *value = kwargs == NULL ? NULL : given_value(kwargs, field, &consumed, type, &error);
        if (error) goto fail;

        PyObject *validated;
        if (value != NULL) validated = validate_field(field, value);
        else if (field->cached != NULL) validated = Py_NewRef(field->cached);
        else validated = PyObject_CallNoArgs(field->default_value);
        if (validated == NULL) {
            report_failure(type, kwargs, field->name);
            goto fail;
        }
        items[i] = validated;
    }

    if (kwargs != NULL && consumed != PyDict_GET_SIZE(kwargs)) {
        raise_key_error(type, kwargs);
        goto fail;
    }
    return 0;

fail:
    for (Py_ssize_t i = 0; i < n; i++) Py_CLEAR(items[i]);
    return -1;
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

    Py_ssize_t n = plan->count < Py_SIZE(self) ? plan->count : Py_SIZE(self);
    StructObject *copy = (StructObject *)type->tp_alloc(type, n);
    if (copy == NULL) return NULL;
    PyObject **source = ITEMS(self);
    PyObject **target = ITEMS(copy);
    Py_ssize_t consumed = 0;
    int error = 0;

    for (Py_ssize_t i = 0; i < n; i++) {
        FieldPlan *field = &plan->fields[i];
        PyObject *value = given_value(kwargs, field, &consumed, type, &error);
        if (error) goto fail;

        if (value == NULL) {
            /* an absent key carries the attribute over - a MISSING value clears it */
            target[i] = Py_XNewRef(source[i]);
            continue;
        }

        PyObject *validated = validate_field(field, value);
        if (validated == NULL) {
            report_failure(type, kwargs, field->name);
            goto fail;
        }
        target[i] = validated;
    }

    if (consumed != PyDict_GET_SIZE(kwargs)) {
        raise_key_error(type, kwargs);
        goto fail;
    }
    return (PyObject *)copy;

fail:
    Py_DECREF(copy);
    return NULL;
}

/*
 * Which keyword argument each attribute was given in, -1 for none - decided before any
 * value is looked at. 0 when the call is not one this handles: a keyword which is not
 * an attribute (or not spelled with the very string of its name), or one attribute
 * given twice.
 */
static int
match_keywords(CompiledPlan *plan, PyObject *kwnames, Py_ssize_t nkw, int *given)
{
    for (Py_ssize_t i = 0; i < plan->count; i++) given[i] = -1;
    for (Py_ssize_t k = 0; k < nkw; k++) {
        PyObject *key = PyTuple_GET_ITEM(kwnames, k);
        Py_ssize_t i = 0;
        for (; i < plan->count; i++) {
            FieldPlan *field = &plan->fields[i];
            if (key == field->name || key == field->alias) break;
        }
        if (i == plan->count || given[i] >= 0) return 0;
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
    CompiledPlan *plan = nkw <= FAST_MAX_KEYWORDS && nargs == 0 ? compiled_plan_of(type) : NULL;
    int given[FAST_MAX_FIELDS];

    if (plan == NULL || !match_keywords(plan, kwnames, nkw, given)) {
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

    StructObject *self = (StructObject *)type->tp_alloc(type, plan->count);
    if (self == NULL) return NULL;
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < plan->count; i++) {
        FieldPlan *field = &plan->fields[i];
        PyObject *validated;
        if (given[i] >= 0) validated = validate_field(field, args[nargs + given[i]]);
        else if (field->cached != NULL) validated = Py_NewRef(field->cached);
        else validated = PyObject_CallNoArgs(field->default_value);
        if (validated == NULL) {
            report_failure(type, NULL, field->name);
            Py_DECREF(self);
            return NULL;
        }
        items[i] = validated;
    }
    return (PyObject *)self;
}

/*
 * An instance built from the items of a mapping. A key which is not an attribute is
 * reported first, before any of the values is validated.
 */
static PyObject *
instance_from_mapping(PyObject *cls, PyObject *mapping)
{
    PyTypeObject *type = (PyTypeObject *)cls;
    CompiledPlan *plan = compiled_plan_of(type);

    PyObject *kwargs = NULL;
    if (PyDict_CheckExact(mapping)) {
        kwargs = Py_NewRef(mapping);
    } else {
        kwargs = PyDict_New();
        if (kwargs == NULL || PyDict_Update(kwargs, mapping) < 0) {
            Py_XDECREF(kwargs);
            return NULL;
        }
    }

    if (plan != NULL) {
        PyObject *key;
        PyObject *value;
        Py_ssize_t position = 0;
        while (PyDict_Next(kwargs, &position, &key, &value)) {
            int known = PySet_Contains(plan->allowed, key);
            if (known < 0) {
                Py_DECREF(kwargs);
                return NULL;
            }
            if (!known) {
                PyObject *r = PyObject_CallFunctionObjArgs(hook_unexpected, cls, key, NULL);
                Py_XDECREF(r);
                Py_DECREF(kwargs);
                return NULL;
            }
        }
    }

    PyObject *empty = PyTuple_New(0);
    PyObject *self = empty == NULL ? NULL : struct_new(type, empty, kwargs);
    if (self != NULL && struct_init(self, empty, kwargs) < 0) Py_CLEAR(self);
    Py_XDECREF(empty);
    Py_DECREF(kwargs);
    return self;
}

/* validate(value) - the instance as it is, an instance built from a mapping, or an error */
static PyObject *
struct_validate(PyObject *cls, PyObject *value)
{
    int matching = PyObject_IsInstance(value, cls);
    if (matching < 0) return NULL;
    if (matching) return Py_NewRef(value);

    int mapping = PyDict_Check(value) ? 1 : PyObject_IsInstance(value, hook_mapping);
    if (mapping < 0) return NULL;
    if (mapping) return instance_from_mapping(cls, value);

    PyErr_Format(PyExc_TypeError, "'%s' is not matching expected type of '%S'", Py_TYPE(value)->tp_name, cls);
    return NULL;
}

/* __replace__(**values) without a dictionary of the values for the usual call */
static PyObject *
struct_replace_fast(PyObject *self, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames)
{
    Py_ssize_t nkw = kwnames == NULL ? 0 : PyTuple_GET_SIZE(kwnames);
    if (nargs == 0 && nkw == 0) return Py_NewRef(self);

    PyTypeObject *type = Py_TYPE(self);
    CompiledPlan *plan = nkw <= FAST_MAX_KEYWORDS && nargs == 0 ? compiled_plan_of(type) : NULL;
    int given[FAST_MAX_FIELDS];
    if (plan == NULL || plan->count > FAST_MAX_FIELDS || plan->count > Py_SIZE(self)
        || !match_keywords(plan, kwnames, nkw, given)) {
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

    StructObject *copy = (StructObject *)type->tp_alloc(type, plan->count);
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
        PyObject *validated = validate_field(field, args[nargs + given[i]]);
        if (validated == NULL) {
            report_failure(type, NULL, field->name);
            Py_DECREF(copy);
            return NULL;
        }
        target[i] = validated;
    }
    return (PyObject *)copy;
}

/* values which are their own deep copy */
static inline int
is_atomic(PyObject *value)
{
    return value == Py_None || PyUnicode_CheckExact(value) || PyLong_CheckExact(value)
        || PyFloat_CheckExact(value) || PyBool_Check(value) || PyBytes_CheckExact(value);
}

/* __deepcopy__(memo) - the copy is entered into the memo before its values are copied,
 * which is what lets a value referring back to the instance resolve to the copy */
static PyObject *
struct_deepcopy(PyObject *self, PyObject *memo)
{
    static PyObject *deepcopy = NULL;
    if (deepcopy == NULL) {
        PyObject *module = PyImport_ImportModule("copy");
        if (module == NULL) return NULL;
        deepcopy = PyObject_GetAttrString(module, "deepcopy");
        Py_DECREF(module);
        if (deepcopy == NULL) return NULL;
    }
    PyObject *own_memo = NULL;
    if (memo == Py_None) {
        own_memo = PyDict_New();
        if (own_memo == NULL) return NULL;
        memo = own_memo;
    }
    PyTypeObject *type = Py_TYPE(self);
    Py_ssize_t n = Py_SIZE(self);
    PyObject *copy = type->tp_alloc(type, n);
    if (copy == NULL) goto fail;
    PyObject *key = PyLong_FromVoidPtr(self);
    if (key == NULL || PyObject_SetItem(memo, key, copy) < 0) {
        Py_XDECREF(key);
        Py_DECREF(copy);
        goto fail;
    }
    Py_DECREF(key);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = ITEMS(self)[i];
        if (value == NULL) continue;
        PyObject *copied = is_atomic(value) ? Py_NewRef(value)
                                            : PyObject_CallFunctionObjArgs(deepcopy, value, memo, NULL);
        if (copied == NULL) {
            Py_DECREF(copy);
            goto fail;
        }
        ITEMS(copy)[i] = copied;
    }
    Py_XDECREF(own_memo);
    return copy;
fail:
    Py_XDECREF(own_memo);
    return NULL;
}
