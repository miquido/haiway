/* ------------------------------------------------------- serialization */

/*
 * The conversion of a value graph, the way the Python walk does it - with the
 * leaves and everything this does not know the spelling of left to it. The path of
 * a value is only ever needed to word an error, so it is kept as a chain of the
 * steps taken, and spelled when something has to be told it.
 */
typedef struct PathNode {
    struct PathNode *parent;
    PyObject *key;      /* the key of the step, or NULL for an index */
    Py_ssize_t index;
} PathNode;

static PyObject *
path_tuple(PathNode *node, PyObject *prefix)
{
    Py_ssize_t depth = 0;
    for (PathNode *n = node; n != NULL; n = n->parent) depth++;
    Py_ssize_t head = PyTuple_GET_SIZE(prefix);
    PyObject *result = PyTuple_New(head + depth);
    if (result == NULL) return NULL;
    for (Py_ssize_t i = 0; i < head; i++) PyTuple_SET_ITEM(result, i, Py_NewRef(PyTuple_GET_ITEM(prefix, i)));
    Py_ssize_t at = head + depth - 1;
    for (PathNode *n = node; n != NULL; n = n->parent, at--) {
        PyObject *step;
        if (n->key != NULL) {
            PyObject *text = PyObject_Str(n->key);
            step = text == NULL ? NULL : PyUnicode_FromFormat("[\"%U\"]", text);
            Py_XDECREF(text);
        } else {
            step = PyUnicode_FromFormat("[%zd]", n->index);
        }
        if (step == NULL) {
            Py_DECREF(result);
            return NULL;
        }
        PyTuple_SET_ITEM(result, at, step);
    }
    return result;
}

/* what a basic value is already - a `str` or an `int`, which an enum of either is too */
static inline int
is_basic_leaf(PyObject *value)
{
    return value == Py_None || PyUnicode_Check(value) || PyLong_Check(value) || PyFloat_Check(value);
}

/* what the conversion to a mapping leaves as it is - the bytes with it */
static inline int
is_mapping_leaf(PyObject *value)
{
    return is_basic_leaf(value) || PyBytes_Check(value);
}

/* an instance of one of the types, told by the type itself first - a subclass through isinstance */
static inline int
instance_of(PyObject *value, PyObject *type)
{
    if (Py_IS_TYPE(value, (PyTypeObject *)type)) return 1;
    return PyObject_IsInstance(value, type);
}

/* the spelling of a value which has one of its own, a new reference - NULL with no error for none */
static PyObject *
native_spelling(ModuleState *state, PyObject *value)
{
    int r = instance_of(value, state->uuid);
    if (r < 0) return NULL;
    if (r) return PyObject_Str(value);

    r = instance_of(value, state->datetime);
    if (r == 0) r = instance_of(value, state->date);
    if (r == 0) r = instance_of(value, state->time);
    if (r < 0) return NULL;
    if (r) return PyObject_CallMethodNoArgs(value, state->str_isoformat);

    r = instance_of(value, state->path);
    if (r < 0) return NULL;
    if (r) return PyObject_CallMethodNoArgs(value, state->str_as_posix);

    return NULL;
}

/*
 * The items of a mapping walked as a dictionary - a dictionary through a snapshot, as
 * nothing here reads what another thread could be changing while the values are
 * converted, a frozendict as it is. `dict` is what is walked, NULL for anything else.
 */
static PyObject *
dict_source(PyObject *value)
{
    if (PyFrozenDict_Check(value)) return Py_NewRef(value);
    if (PyDict_Check(value)) return PyDict_Copy(value);
    return NULL;
}

static PyObject *basic_convert_unbounded(ModuleState *state, PyObject *value, int strict, PathNode *node, PyObject *prefix);

static PyObject *
basic_convert(ModuleState *state, PyObject *value, int strict, PathNode *node, PyObject *prefix)
{
    if (Py_EnterRecursiveCall(" while converting a State")) return NULL;
    PyObject *result = basic_convert_unbounded(state, value, strict, node, prefix);
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *
basic_struct(ModuleState *state, PyObject *self, int strict, PathNode *node, PyObject *prefix)
{
    CompiledPlan *plan = plan_or_error(Py_TYPE(self));
    if (plan == NULL) return NULL;
    PyObject *result = PyDict_New();
    if (result == NULL) return NULL;
    Py_ssize_t n = plan->count;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = ITEMS(self)[i];  /* bound once, for good - read as it is */
        if (value == NULL || value == state->missing) continue;
        FieldPlan *field = &plan->fields[i];
        PyObject *key = field_key(field);
        PathNode step = {node, key, 0};
        PyObject *converted = basic_convert(state, value, strict, &step, prefix);
        if (converted == NULL || PyDict_SetItem(result, key, converted) < 0) {
            Py_XDECREF(converted);
            Py_DECREF(result);
            return NULL;
        }
        Py_DECREF(converted);
    }
    PyObject *mapped = PyObject_CallOneArg(state->map, result);
    Py_DECREF(result);
    return mapped;
}

static PyObject *
basic_convert_unbounded(ModuleState *state, PyObject *value, int strict, PathNode *node, PyObject *prefix)
{
    if (is_basic_leaf(value)) return Py_NewRef(value);
    if (is_struct(state, value)) return basic_struct(state, value, strict, node, prefix);

    if (PyTuple_CheckExact(value) || PyList_CheckExact(value)) {
        PyObject *items = PySequence_Tuple(value);  /* a snapshot of a list, the tuple itself otherwise */
        if (items == NULL) return NULL;
        Py_ssize_t n = PyTuple_GET_SIZE(items);
        PyObject *result = PyTuple_New(n);
        if (result == NULL) {
            Py_DECREF(items);
            return NULL;
        }
        for (Py_ssize_t i = 0; i < n; i++) {
            PathNode step = {node, NULL, i};
            PyObject *converted = basic_convert(state, PyTuple_GET_ITEM(items, i), strict, &step, prefix);
            if (converted == NULL) {
                Py_DECREF(items);
                Py_DECREF(result);
                return NULL;
            }
            PyTuple_SET_ITEM(result, i, converted);
        }
        Py_DECREF(items);
        return result;
    }

    PyObject *spelled = native_spelling(state, value);
    if (spelled != NULL || PyErr_Occurred()) return spelled;

    PyObject *source = dict_source(value);
    if (source == NULL && PyErr_Occurred()) return NULL;
    if (source != NULL) {
        PyObject *result = PyDict_New();
        if (result == NULL) {
            Py_DECREF(source);
            return NULL;
        }
        PyObject *key;
        PyObject *item;
        Py_ssize_t position = 0;
        while (PyDict_Next(source, &position, &key, &item)) {
            PyObject *key_basic;
            if (PyUnicode_CheckExact(key)) {
                key_basic = Py_NewRef(key);
            } else {
                PyObject *path = path_tuple(node, prefix);
                if (path == NULL) goto items_fail;
                key_basic = PyObject_CallFunction(state->basic_key, "OOO", key, strict ? Py_True : Py_False, path);
                Py_DECREF(path);
                if (key_basic == NULL) goto items_fail;
            }
            PathNode step = {node, key_basic, 0};
            PyObject *converted = basic_convert(state, item, strict, &step, prefix);
            if (converted == NULL || PyDict_SetItem(result, key_basic, converted) < 0) {
                Py_XDECREF(converted);
                Py_DECREF(key_basic);
                goto items_fail;
            }
            Py_DECREF(converted);
            Py_DECREF(key_basic);
        }
        Py_DECREF(source);
        PyObject *mapped = PyObject_CallOneArg(state->map, result);
        Py_DECREF(result);
        return mapped;
items_fail:
        Py_DECREF(source);
        Py_DECREF(result);
        return NULL;
    }

    /* everything else is for the leaf table and the less common containers of Python */
    PyObject *path = path_tuple(node, prefix);
    if (path == NULL) return NULL;
    PyObject *converted = PyObject_CallFunction(state->basic_value, "OOO", value, strict ? Py_True : Py_False, path);
    Py_DECREF(path);
    return converted;
}

/* basic_object(instance, strict, path) */
static PyObject *
basic_object(PyObject *mod, PyObject *args)
{
    ModuleState *state = module_state(mod);
    PyObject *self;
    int strict;
    PyObject *prefix;
    if (!PyArg_ParseTuple(args, "OpO!", &self, &strict, &PyTuple_Type, &prefix)) return NULL;
    if (!is_struct(state, self)) {
        PyErr_SetString(PyExc_TypeError, "basic_object expects a State");
        return NULL;
    }
    return basic_struct(state, self, strict, NULL, prefix);
}

/* a uuid, a date or a path - 1 for what can't change and has no copy to make, 0 otherwise */
static int
is_known_immutable(ModuleState *state, PyObject *value)
{
    int r = instance_of(value, state->uuid);
    if (r != 0) return r;
    r = instance_of(value, state->temporal);
    if (r != 0) return r;
    return instance_of(value, state->path);
}

static PyObject *recursive_convert_unbounded(ModuleState *state, PyObject *value);

static PyObject *
recursive_convert(ModuleState *state, PyObject *value)
{
    if (Py_EnterRecursiveCall(" while converting a State")) return NULL;
    PyObject *result = recursive_convert_unbounded(state, value);
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *
recursive_struct(ModuleState *state, PyObject *self, int recursive)
{
    CompiledPlan *plan = plan_or_error(Py_TYPE(self));
    if (plan == NULL) return NULL;
    PyObject *result = PyDict_New();
    if (result == NULL) return NULL;
    Py_ssize_t n = plan->count;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = ITEMS(self)[i];  /* bound once, for good - read as it is */
        if (value == NULL || value == state->missing) continue;
        PyObject *key = field_key(&plan->fields[i]);
        PyObject *converted = recursive ? recursive_convert(state, value) : Py_NewRef(value);
        if (converted == NULL || PyDict_SetItem(result, key, converted) < 0) {
            Py_XDECREF(converted);
            Py_DECREF(result);
            return NULL;
        }
        Py_DECREF(converted);
    }
    return result;
}

static PyObject *
recursive_convert_unbounded(ModuleState *state, PyObject *value)
{
    if (is_mapping_leaf(value)) return Py_NewRef(value);
    if (is_struct(state, value)) return recursive_struct(state, value, 1);

    if (PyTuple_CheckExact(value) || PyList_CheckExact(value)) {
        PyObject *items = PySequence_Tuple(value);  /* a snapshot of a list, the tuple itself otherwise */
        if (items == NULL) return NULL;
        Py_ssize_t n = PyTuple_GET_SIZE(items);
        PyObject *result = PyList_New(n);
        if (result == NULL) {
            Py_DECREF(items);
            return NULL;
        }
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *converted = recursive_convert(state, PyTuple_GET_ITEM(items, i));
            if (converted == NULL) {
                Py_DECREF(items);
                Py_DECREF(result);
                return NULL;
            }
            PyList_SET_ITEM(result, i, converted);
        }
        Py_DECREF(items);
        return result;
    }

    int immutable = is_known_immutable(state, value);
    if (immutable < 0) return NULL;
    if (immutable) return Py_NewRef(value);

    PyObject *source = dict_source(value);
    if (source == NULL && PyErr_Occurred()) return NULL;
    if (source != NULL) {
        PyObject *result = PyDict_New();
        if (result == NULL) {
            Py_DECREF(source);
            return NULL;
        }
        PyObject *key;
        PyObject *item;
        Py_ssize_t position = 0;
        while (PyDict_Next(source, &position, &key, &item)) {
            PyObject *converted = recursive_convert(state, item);
            if (converted == NULL || PyDict_SetItem(result, key, converted) < 0) {
                Py_XDECREF(converted);
                Py_DECREF(source);
                Py_DECREF(result);
                return NULL;
            }
            Py_DECREF(converted);
        }
        Py_DECREF(source);
        return result;
    }

    return PyObject_CallOneArg(state->recursive, value);
}

/* to_mapping(instance, recursive) */
static PyObject *
to_mapping(PyObject *mod, PyObject *args)
{
    ModuleState *state = module_state(mod);
    PyObject *self;
    int recursive;
    if (!PyArg_ParseTuple(args, "Op", &self, &recursive)) return NULL;
    if (!is_struct(state, self)) {
        PyErr_SetString(PyExc_TypeError, "to_mapping expects a State");
        return NULL;
    }
    return recursive_struct(state, self, recursive);
}
