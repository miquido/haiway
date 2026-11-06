/* ----------------------------------------------------------- the rest */

static int
struct_traverse(PyObject *self, visitproc visit, void *arg)
{
    Py_ssize_t n = Py_SIZE(self);
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < n; i++) Py_VISIT(items[i]);
    return 0;
}

static int
struct_clear(PyObject *self)
{
    Py_ssize_t n = Py_SIZE(self);
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < n; i++) Py_CLEAR(items[i]);
    return 0;
}

static void
struct_dealloc(PyObject *self)
{
    PyTypeObject *tp = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    Py_ssize_t n = Py_SIZE(self);
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < n; i++) Py_CLEAR(items[i]);
    tp->tp_free(self);
}

static int
struct_setattro(PyObject *self, PyObject *name, PyObject *value)
{
    PyObject *qualname = PyType_GetQualName(Py_TYPE(self));
    if (qualname != NULL) {
        PyErr_Format(
            PyExc_AttributeError,
            "Can't modify immutable state %U, attribute - '%U' cannot be %s",
            qualname, name, value == NULL ? "deleted" : "modified");
        Py_DECREF(qualname);
    }
    return -1;
}

static Py_hash_t
struct_hash_unbounded(PyObject *self)
{
    Py_ssize_t n = Py_SIZE(self);
    PyObject **items = ITEMS(self);
    Py_uhash_t acc = (Py_uhash_t)(uintptr_t)Py_TYPE(self) * 1000003UL;
    for (Py_ssize_t i = 0; i < n; i++) {
        if (items[i] == NULL || items[i] == hook_missing) continue;  /* MISSING never takes part */
        Py_hash_t h = PyObject_Hash(items[i]);
        if (h == -1) {
            if (!PyErr_ExceptionMatches(PyExc_TypeError)) return -1;
            PyErr_Clear();  /* unhashable values are skipped */
            continue;
        }
        acc = (acc ^ (Py_uhash_t)h) * 1000003UL;
        acc ^= acc >> 29;
    }
    Py_hash_t r = (Py_hash_t)acc;
    return r == -1 ? -2 : r;
}

static Py_hash_t
struct_hash(PyObject *self)
{
    if (Py_EnterRecursiveCall(" while hashing a State")) return -1;
    Py_hash_t result = struct_hash_unbounded(self);
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *
struct_richcompare(PyObject *a, PyObject *b, int op)
{
    if (op != Py_EQ && op != Py_NE) Py_RETURN_NOTIMPLEMENTED;
    int eq = 1;
    if (a != b) {
        if (Py_TYPE(a) != Py_TYPE(b)) {
            eq = 0;  /* only the same concrete class is ever equal */
        } else {
            Py_ssize_t n = Py_SIZE(a);
            for (Py_ssize_t i = 0; i < n && eq; i++) {
                PyObject *x = ITEMS(a)[i], *y = ITEMS(b)[i];
                if (x == NULL || y == NULL) {  /* never initialized */
                    eq = (x == y);
                    continue;
                }
                /* not an identity shortcut - a NaN is not equal to itself */
                PyObject *r = PyObject_RichCompare(x, y, Py_EQ);
                if (r == NULL) return NULL;
                eq = PyObject_IsTrue(r);
                Py_DECREF(r);
                if (eq < 0) return NULL;
            }
        }
    }
    return PyBool_FromLong(op == Py_EQ ? eq : !eq);
}
