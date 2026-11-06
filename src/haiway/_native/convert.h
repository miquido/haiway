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

/* the spelling of a value which has one of its own, a new reference - NULL with no error for none */
static PyObject *
native_spelling(PyObject *value)
{
    int r = PyObject_IsInstance(value, hook_uuid);
    if (r < 0) return NULL;
    if (r) return PyObject_Str(value);

    r = PyObject_IsInstance(value, hook_temporal);
    if (r < 0) return NULL;
    if (r) return PyObject_CallMethodNoArgs(value, str_isoformat);

    r = PyObject_IsInstance(value, hook_path);
    if (r < 0) return NULL;
    if (r) return PyObject_CallMethodNoArgs(value, str_as_posix);

    return NULL;
}

static inline int
is_struct(PyObject *value)
{
    return PyType_IsSubtype(Py_TYPE(value), &StructType);
}

/* a mapping the walk treats as a dictionary: its items as a new list, NULL if it is not one */
static PyObject *
mapping_items(PyObject *value, int *error)
{
    int r = PyObject_IsInstance(value, hook_map_types);
    if (r < 0) {
        *error = 1;
        return NULL;
    }
    if (r == 0) return NULL;
    PyObject *items = PyMapping_Items(value);
    if (items == NULL) *error = 1;
    return items;
}

static PyObject *basic_convert_unbounded(PyObject *value, int strict, PathNode *node, PyObject *prefix);

static PyObject *
basic_convert(PyObject *value, int strict, PathNode *node, PyObject *prefix)
{
    if (Py_EnterRecursiveCall(" while converting a State")) return NULL;
    PyObject *result = basic_convert_unbounded(value, strict, node, prefix);
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *
basic_struct(PyObject *self, int strict, PathNode *node, PyObject *prefix)
{
    CompiledPlan *plan = plan_or_error(Py_TYPE(self));
    if (plan == NULL) return NULL;
    PyObject *result = PyDict_New();
    if (result == NULL) return NULL;
    Py_ssize_t n = plan->count < Py_SIZE(self) ? plan->count : Py_SIZE(self);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = ITEMS(self)[i];
        if (value == NULL || value == hook_missing) continue;
        FieldPlan *field = &plan->fields[i];
        PyObject *key = field_key(field);
        PathNode step = {node, key, 0};
        PyObject *converted = basic_convert(value, strict, &step, prefix);
        if (converted == NULL || PyDict_SetItem(result, key, converted) < 0) {
            Py_XDECREF(converted);
            Py_DECREF(result);
            return NULL;
        }
        Py_DECREF(converted);
    }
    PyObject *mapped = PyObject_CallOneArg(hook_map, result);
    Py_DECREF(result);
    return mapped;
}

static PyObject *
basic_convert_unbounded(PyObject *value, int strict, PathNode *node, PyObject *prefix)
{
    if (is_basic_leaf(value)) return Py_NewRef(value);
    if (is_struct(value)) return basic_struct(value, strict, node, prefix);

    if (PyTuple_CheckExact(value) || PyList_CheckExact(value)) {
        Py_ssize_t n = PySequence_Fast_GET_SIZE(value);
        PyObject *result = PyTuple_New(n);
        if (result == NULL) return NULL;
        for (Py_ssize_t i = 0; i < n; i++) {
            PathNode step = {node, NULL, i};
            PyObject *converted = basic_convert(PySequence_Fast_GET_ITEM(value, i), strict, &step, prefix);
            if (converted == NULL) {
                Py_DECREF(result);
                return NULL;
            }
            PyTuple_SET_ITEM(result, i, converted);
        }
        return result;
    }

    PyObject *spelled = native_spelling(value);
    if (spelled != NULL || PyErr_Occurred()) return spelled;

    int error = 0;
    PyObject *items = mapping_items(value, &error);
    if (error) return NULL;
    if (items != NULL) {
        PyObject *result = PyDict_New();
        if (result == NULL) {
            Py_DECREF(items);
            return NULL;
        }
        for (Py_ssize_t i = 0; i < PyList_GET_SIZE(items); i++) {
            PyObject *pair = PyList_GET_ITEM(items, i);
            PyObject *key = PyTuple_GET_ITEM(pair, 0);
            PyObject *key_basic;
            if (PyUnicode_CheckExact(key)) {
                key_basic = Py_NewRef(key);
            } else {
                PyObject *path = path_tuple(node, prefix);
                if (path == NULL) goto items_fail;
                key_basic = PyObject_CallFunction(hook_basic_key, "OOO", key, strict ? Py_True : Py_False, path);
                Py_DECREF(path);
                if (key_basic == NULL) goto items_fail;
            }
            PathNode step = {node, key_basic, 0};
            PyObject *converted = basic_convert(PyTuple_GET_ITEM(pair, 1), strict, &step, prefix);
            if (converted == NULL || PyDict_SetItem(result, key_basic, converted) < 0) {
                Py_XDECREF(converted);
                Py_DECREF(key_basic);
                goto items_fail;
            }
            Py_DECREF(converted);
            Py_DECREF(key_basic);
        }
        Py_DECREF(items);
        PyObject *mapped = PyObject_CallOneArg(hook_map, result);
        Py_DECREF(result);
        return mapped;
items_fail:
        Py_DECREF(items);
        Py_DECREF(result);
        return NULL;
    }

    /* everything else is for the leaf table and the less common containers of Python */
    PyObject *path = path_tuple(node, prefix);
    if (path == NULL) return NULL;
    PyObject *converted = PyObject_CallFunction(hook_basic_value, "OOO", value, strict ? Py_True : Py_False, path);
    Py_DECREF(path);
    return converted;
}

/* basic_object(instance, strict, path) */
static PyObject *
basic_object(PyObject *mod, PyObject *args)
{
    PyObject *self;
    int strict;
    PyObject *prefix;
    if (!PyArg_ParseTuple(args, "OpO!", &self, &strict, &PyTuple_Type, &prefix)) return NULL;
    if (!is_struct(self)) {
        PyErr_SetString(PyExc_TypeError, "basic_object expects a State");
        return NULL;
    }
    return basic_struct(self, strict, NULL, prefix);
}

/* a uuid, a date or a path - 1 for what can't change and has no copy to make, 0 otherwise */
static int
is_known_immutable(PyObject *value)
{
    int r = PyObject_IsInstance(value, hook_uuid);
    if (r != 0) return r;
    r = PyObject_IsInstance(value, hook_temporal);
    if (r != 0) return r;
    return PyObject_IsInstance(value, hook_path);
}

static PyObject *recursive_convert_unbounded(PyObject *value);

static PyObject *
recursive_convert(PyObject *value)
{
    if (Py_EnterRecursiveCall(" while converting a State")) return NULL;
    PyObject *result = recursive_convert_unbounded(value);
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *
recursive_struct(PyObject *self, int recursive)
{
    CompiledPlan *plan = plan_or_error(Py_TYPE(self));
    if (plan == NULL) return NULL;
    PyObject *result = PyDict_New();
    if (result == NULL) return NULL;
    Py_ssize_t n = plan->count < Py_SIZE(self) ? plan->count : Py_SIZE(self);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = ITEMS(self)[i];
        if (value == NULL || value == hook_missing) continue;
        PyObject *key = field_key(&plan->fields[i]);
        PyObject *converted = recursive ? recursive_convert(value) : Py_NewRef(value);
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
recursive_convert_unbounded(PyObject *value)
{
    if (is_mapping_leaf(value)) return Py_NewRef(value);
    if (is_struct(value)) return recursive_struct(value, 1);

    if (PyTuple_CheckExact(value) || PyList_CheckExact(value)) {
        Py_ssize_t n = PySequence_Fast_GET_SIZE(value);
        PyObject *result = PyList_New(n);
        if (result == NULL) return NULL;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *converted = recursive_convert(PySequence_Fast_GET_ITEM(value, i));
            if (converted == NULL) {
                Py_DECREF(result);
                return NULL;
            }
            PyList_SET_ITEM(result, i, converted);
        }
        return result;
    }

    int immutable = is_known_immutable(value);
    if (immutable < 0) return NULL;
    if (immutable) return Py_NewRef(value);

    int error = 0;
    PyObject *items = mapping_items(value, &error);
    if (error) return NULL;
    if (items != NULL) {
        PyObject *result = PyDict_New();
        if (result == NULL) {
            Py_DECREF(items);
            return NULL;
        }
        for (Py_ssize_t i = 0; i < PyList_GET_SIZE(items); i++) {
            PyObject *pair = PyList_GET_ITEM(items, i);
            PyObject *converted = recursive_convert(PyTuple_GET_ITEM(pair, 1));
            if (converted == NULL || PyDict_SetItem(result, PyTuple_GET_ITEM(pair, 0), converted) < 0) {
                Py_XDECREF(converted);
                Py_DECREF(items);
                Py_DECREF(result);
                return NULL;
            }
            Py_DECREF(converted);
        }
        Py_DECREF(items);
        return result;
    }

    return PyObject_CallOneArg(hook_recursive, value);
}

/* to_mapping(instance, recursive) */
static PyObject *
to_mapping(PyObject *mod, PyObject *args)
{
    PyObject *self;
    int recursive;
    if (!PyArg_ParseTuple(args, "Op", &self, &recursive)) return NULL;
    if (!is_struct(self)) {
        PyErr_SetString(PyExc_TypeError, "to_mapping expects a State");
        return NULL;
    }
    return recursive_struct(self, recursive);
}
