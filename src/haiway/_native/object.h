/* ----------------------------------------------------------- the rest */

static int
struct_traverse(PyObject *self, visitproc visit, void *arg)
{
    Py_VISIT(Py_TYPE(self));
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < n; i++) Py_VISIT(items[i]);
    return 0;
}

static int
struct_clear(PyObject *self)
{
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < n; i++) Py_CLEAR(items[i]);
    return 0;
}

static void
struct_dealloc(PyObject *self)
{
    PyTypeObject *tp = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    struct_clear(self);
    tp->tp_free(self);
    Py_DECREF(tp);  /* the type is a heap type, which its instances keep alive */
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

/* the MISSING marker, which the values of an instance are compared with by identity */
static inline PyObject *
missing_of(PyObject *self)
{
    CompiledPlan *plan = PLAN_OF(Py_TYPE(self));
    return plan == NULL ? NULL : plan->state->missing;
}

static Py_hash_t
struct_hash_unbounded(PyObject *self)
{
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject **items = ITEMS(self);
    PyObject *missing = missing_of(self);
    Py_uhash_t acc = (Py_uhash_t)(uintptr_t)Py_TYPE(self) * 1000003UL;
    for (Py_ssize_t i = 0; i < n; i++) {
        if (items[i] == NULL || items[i] == missing) continue;  /* MISSING never takes part */
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
            Py_ssize_t n = ITEM_COUNT(a);
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

/* ------------------------------------------------------------ metaclass */

static int
structmeta_traverse(PyObject *self, visitproc visit, void *arg)
{
    CompiledPlan *plan = PLAN_OF(self);
    if (plan != NULL) {
        Py_VISIT(plan->owner);
        Py_VISIT(plan->keys);
        Py_VISIT(plan->cached);
    }
    return PyType_Type.tp_traverse(self, visit, arg);
}

static int
structmeta_clear(PyObject *self)
{
    CompiledPlan *plan = PLAN_OF(self);
    if (plan != NULL) {
        /* the names stay - the descriptors of the class read them until it is freed */
        Py_CLEAR(plan->owner);
        Py_CLEAR(plan->keys);
        Py_CLEAR(plan->cached);
    }
    return PyType_Type.tp_clear(self);
}

static void
structmeta_dealloc(PyObject *self)
{
    PyTypeObject *metatype = Py_TYPE(self);
    CompiledPlan *plan = PLAN_OF(self);
    PLAN_OF(self) = NULL;
    PyType_Type.tp_dealloc(self);  /* clears the class, its descriptors with it */
    plan_free(plan);
    Py_DECREF(metatype);
}
