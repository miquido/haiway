static inline PyObject *
lookup(PyObject *mapping, PyObject *key, int *error)
{
    PyObject *value = PyDict_GetItemWithError(mapping, key);
    if (value == NULL && PyErr_Occurred()) *error = 1;
    return value;
}

/*
 * What is accepted as it is, without calling the validator of the annotation:
 * 1 - accepted, `out` holds the validated value (a new reference)
 * 0 - not decided here, the validator has to
 * -1 - an error is set
 */
static int
fast_validate(long kind, PyObject *arg, PyObject *value, PyObject **out)
{
    switch (kind) {
    case K_ANY:
        *out = Py_NewRef(value);
        return 1;
    case K_INT:
        if (!PyLong_CheckExact(value)) return 0;
        break;
    case K_STR:
        if (!PyUnicode_CheckExact(value)) return 0;
        break;
    case K_FLOAT:
        if (PyFloat_CheckExact(value)) break;
        if (PyLong_CheckExact(value)) {  /* read as the float of it */
            double number = PyLong_AsDouble(value);
            if (number == -1.0 && PyErr_Occurred()) {
                PyErr_Clear();
                return 0;
            }
            *out = PyFloat_FromDouble(number);
            return *out == NULL ? -1 : 1;
        }
        return 0;
    case K_FLOAT_EXACT:  /* a float as it is, which an alternative of a union is limited to */
        if (!PyFloat_CheckExact(value)) return 0;
        break;
    case K_BOOL:
        if (!PyBool_Check(value)) return 0;
        break;
    case K_INSTANCE:
        if ((PyObject *)Py_TYPE(value) != arg) return 0;
        break;
    case K_OPTIONAL: {
        PyObject *singletons = PyTuple_GET_ITEM(arg, 0);
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(singletons); i++) {
            if (PyTuple_GET_ITEM(singletons, i) == value) {
                *out = Py_NewRef(value);
                return 1;
            }
        }
        return fast_validate(PyLong_AsLong(PyTuple_GET_ITEM(arg, 1)), PyTuple_GET_ITEM(arg, 2), value, out);
    }
    case K_SEQUENCE: {
        int is_tuple = PyTuple_CheckExact(value);
        if (!is_tuple && !PyList_CheckExact(value)) return 0;
        long element_kind = PyLong_AsLong(PyTuple_GET_ITEM(arg, 0));
        PyObject *element_arg = PyTuple_GET_ITEM(arg, 1);
        Py_ssize_t n = PySequence_Fast_GET_SIZE(value);
        PyObject *result = PyTuple_New(n);
        if (result == NULL) return -1;
        int same = is_tuple;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *element = PySequence_Fast_GET_ITEM(value, i);
            PyObject *checked = NULL;
            int r = fast_validate(element_kind, element_arg, element, &checked);
            if (r <= 0) {
                Py_DECREF(result);
                return r;
            }
            if (checked != element) same = 0;
            PyTuple_SET_ITEM(result, i, checked);
        }
        if (same) {
            Py_DECREF(result);
            *out = Py_NewRef(value);
        } else {
            *out = result;
        }
        return 1;
    }
    case K_ISINSTANCE: {
        /* (type, converter, from) - an instance as it is, or the conversion of a str (S_STR)
         * or of an int (S_INT) which has a converter. A conversion which fails is not an error
         * here - the annotation which does not accept the value is the one telling why. */
        int r = PyObject_IsInstance(value, PyTuple_GET_ITEM(arg, 0));
        if (r < 0) return -1;
        if (r) break;
        PyObject *converter = PyTuple_GET_ITEM(arg, 1);
        long from = PyLong_AsLong(PyTuple_GET_ITEM(arg, 2));
        if (converter == Py_None
            || !((from == S_STR && PyUnicode_Check(value)) || (from == S_INT && PyLong_Check(value)))) {
            return 0;
        }
        PyObject *converted = PyObject_CallOneArg(converter, value);
        if (converted == NULL) {
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return -1;
            PyErr_Clear();
            return 0;
        }
        *out = converted;
        return 1;
    }
    case K_LITERAL: {
        int r = PySequence_Contains(arg, value);
        if (r <= 0) return r < 0 ? (PyErr_Clear(), 0) : 0;
        break;
    }
    case K_UNION: {
        /* (singletons, alternatives) - what is None or Missing, then alternatives which only ever
         * accept a value as it is, the first to do so */
        PyObject *singletons = PyTuple_GET_ITEM(arg, 0);
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(singletons); i++) {
            if (PyTuple_GET_ITEM(singletons, i) == value) {
                *out = Py_NewRef(value);
                return 1;
            }
        }
        PyObject *alternatives = PyTuple_GET_ITEM(arg, 1);
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(alternatives); i++) {
            PyObject *alternative = PyTuple_GET_ITEM(alternatives, i);
            PyObject *checked = NULL;
            int r = fast_validate(PyLong_AsLong(PyTuple_GET_ITEM(alternative, 0)), PyTuple_GET_ITEM(alternative, 1), value, &checked);
            if (r < 0) return -1;
            if (r > 0) {
                *out = checked;
                return 1;
            }
        }
        return 0;
    }
    case K_SET: {
        int is_sequence = PyTuple_CheckExact(value) || PyList_CheckExact(value);
        int is_set = PyAnySet_CheckExact(value);
        if (!is_sequence && !is_set) return 0;
        long element_kind = PyLong_AsLong(PyTuple_GET_ITEM(arg, 0));
        PyObject *element_arg = PyTuple_GET_ITEM(arg, 1);
        PyObject *items = is_sequence ? Py_NewRef(value) : PySequence_Tuple(value);
        if (items == NULL) return -1;
        Py_ssize_t n = PySequence_Fast_GET_SIZE(items);
        PyObject *result = PySet_New(NULL);
        if (result == NULL) {
            Py_DECREF(items);
            return -1;
        }
        int same = PyFrozenSet_CheckExact(value);
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *element = PySequence_Fast_GET_ITEM(items, i);
            PyObject *checked = NULL;
            int r = fast_validate(element_kind, element_arg, element, &checked);
            if (r <= 0) {
                Py_DECREF(items);
                Py_DECREF(result);
                return r;
            }
            if (checked != element) same = 0;
            int failed = PySet_Add(result, checked);
            Py_DECREF(checked);
            if (failed < 0) {
                Py_DECREF(items);
                Py_DECREF(result);
                return -1;
            }
        }
        Py_DECREF(items);
        if (same) {
            Py_DECREF(result);
            *out = Py_NewRef(value);
            return 1;
        }
        PyObject *frozen = PyFrozenSet_New(result);
        Py_DECREF(result);
        if (frozen == NULL) return -1;
        *out = frozen;
        return 1;
    }
    case K_TUPLE: {
        int is_tuple = PyTuple_CheckExact(value);
        if (!is_tuple && !PyList_CheckExact(value)) return 0;
        Py_ssize_t n = PySequence_Fast_GET_SIZE(value);
        if (n != PyTuple_GET_SIZE(arg)) return 0;  /* the length is reported by the annotation */
        PyObject *result = PyTuple_New(n);
        if (result == NULL) return -1;
        int same = is_tuple;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *position = PyTuple_GET_ITEM(arg, i);
            PyObject *element = PySequence_Fast_GET_ITEM(value, i);
            PyObject *checked = NULL;
            int r = fast_validate(PyLong_AsLong(PyTuple_GET_ITEM(position, 0)), PyTuple_GET_ITEM(position, 1), element, &checked);
            if (r <= 0) {
                Py_DECREF(result);
                return r;
            }
            if (checked != element) same = 0;
            PyTuple_SET_ITEM(result, i, checked);
        }
        if (same) {
            Py_DECREF(result);
            *out = Py_NewRef(value);
        } else {
            *out = result;
        }
        return 1;
    }
    case K_MAPPING: {
        if (!PyDict_CheckExact(value)) return 0;
        long key_kind = PyLong_AsLong(PyTuple_GET_ITEM(arg, 0));
        long value_kind = PyLong_AsLong(PyTuple_GET_ITEM(arg, 2));
        PyObject *result = PyDict_New();
        if (result == NULL) return -1;
        PyObject *key;
        PyObject *item;
        Py_ssize_t position = 0;
        while (PyDict_Next(value, &position, &key, &item)) {
            PyObject *checked_key = NULL;
            PyObject *checked_item = NULL;
            int r = fast_validate(key_kind, PyTuple_GET_ITEM(arg, 1), key, &checked_key);
            if (r <= 0) {
                Py_DECREF(result);
                return r;
            }
            r = fast_validate(value_kind, PyTuple_GET_ITEM(arg, 3), item, &checked_item);
            if (r <= 0) {
                Py_DECREF(checked_key);
                Py_DECREF(result);
                return r;
            }
            int failed = PyDict_SetItem(result, checked_key, checked_item);
            Py_DECREF(checked_key);
            Py_DECREF(checked_item);
            if (failed < 0) {
                Py_DECREF(result);
                return -1;
            }
        }
        PyObject *mapped = PyObject_CallOneArg(hook_map, result);
        Py_DECREF(result);
        if (mapped == NULL) return -1;
        *out = mapped;
        return 1;
    }
    default:
        return 0;
    }
    *out = Py_NewRef(value);
    return 1;
}

/*
 * A failed validation. Unexpected and duplicated keys take precedence over it, the
 * way they do when they are checked before any value is validated - so they are
 * checked here, and only here, since a failure is the exception.
 * Leaves an error set - the failure is always raised, never swallowed.
 */
static void
report_failure(PyTypeObject *type, PyObject *kwargs, PyObject *name)
{
    if (!PyErr_ExceptionMatches(PyExc_Exception)) return;  /* not ours to wrap */
    PyObject *exc = PyErr_GetRaisedException();
    if (kwargs != NULL) {
        PyObject *r = PyObject_CallFunctionObjArgs(hook_check, (PyObject *)type, kwargs, NULL);
        if (r == NULL) {
            Py_DECREF(exc);
            return;
        }
        Py_DECREF(r);
    }
    PyObject *path = PyUnicode_FromFormat(".%U", name);
    if (path == NULL) {
        Py_DECREF(exc);
        return;
    }
    PyObject *r = PyObject_CallFunctionObjArgs(hook_report, path, exc, NULL);
    Py_DECREF(path);
    if (r != NULL) {  /* report is documented to raise - make sure nothing is swallowed */
        Py_DECREF(r);
        PyErr_SetRaisedException(exc);
        return;
    }
    Py_DECREF(exc);
}

/* raises the error of unexpected or duplicated keys, which are known to be there */
static int
raise_key_error(PyTypeObject *type, PyObject *kwargs)
{
    PyObject *r = PyObject_CallFunctionObjArgs(hook_check, (PyObject *)type, kwargs, NULL);
    if (r != NULL) {
        Py_DECREF(r);
        PyErr_SetString(PyExc_TypeError, "unexpected attributes");
    }
    return -1;
}
