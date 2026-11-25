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

static Py_hash_t struct_hash(PyObject *self);

enum { HASH_ERROR = -1, HASH_LEFT_OUT = 0, HASH_DONE = 1 };

static inline Py_uhash_t
hash_mixed(Py_uhash_t acc, Py_hash_t h)
{
    acc = (acc ^ (Py_uhash_t)h) * 1000003UL;
    return acc ^ (acc >> 29);
}

static inline Py_hash_t
hash_result(Py_uhash_t acc)
{
    Py_hash_t r = (Py_hash_t)acc;
    return r == -1 ? -2 : r;
}

/* the hashable which stands for a container, as the language compares it: a list, a tuple and
 * any other sequence as a tuple of what they hold, a dictionary and any other mapping as a
 * frozendict, a set and any other set as a frozenset, a bytearray as bytes and a memoryview as
 * the bytes or the tuple of what it holds */
enum { FORM_NONE = 0, FORM_SEQUENCE, FORM_MAPPING, FORM_SET, FORM_BYTES, FORM_VIEW };

static int
form_kind(ModuleState *state, PyObject *value)
{
    if (PyList_Check(value) || PyTuple_Check(value)) return FORM_SEQUENCE;
    if (PyDict_Check(value) || PyFrozenDict_Check(value)) return FORM_MAPPING;
    if (PyAnySet_Check(value)) return FORM_SET;
    if (PyByteArray_Check(value)) return FORM_BYTES;
    if (PyMemoryView_Check(value)) return FORM_VIEW;
    if (state == NULL || PyUnicode_Check(value) || PyBytes_Check(value)) return FORM_NONE;
    int r = PyObject_IsInstance(value, state->mapping);
    if (r != 0) return r > 0 ? FORM_MAPPING : -1;
    r = PyObject_IsInstance(value, state->set);
    if (r != 0) return r > 0 ? FORM_SET : -1;
    r = PyObject_IsInstance(value, state->sequence);
    if (r != 0) return r > 0 ? FORM_SEQUENCE : -1;
    return FORM_NONE;
}

/* whether the hash of the language is the form of a value - which it is for all but the
 * containers which hold others, or are equal to one of another kind: a type with no hash of
 * its own (a list, a dictionary, a set, a bytearray), a tuple, a frozendict and a memoryview.
 * Told from the slot of the type alone, so that the most of the values are done with here */
static inline int
hash_is_plain(PyObject *value)
{
    PyTypeObject *type = Py_TYPE(value);
    hashfunc slot = type->tp_hash;
    return slot != PyObject_HashNotImplemented && slot != PyTuple_Type.tp_hash && slot != PyFrozenDict_Type.tp_hash
        && type != &PyMemoryView_Type;
}

static int hash_form(ModuleState *state, PyObject *value, Py_hash_t *result);
static PyObject *hashable_form(ModuleState *state, PyObject *value);

/* whether a failure to hash with the error set is what a container which can be rebuilt
 * fails with - anything else is the failure of the value, an instance which is not
 * initialized above all */
static int
hash_failure_is_rebuildable(PyObject *value)
{
    return PyErr_ExceptionMatches(PyExc_TypeError) && Py_TYPE(value)->tp_hash != struct_hash;
}

static PyObject *
memoryview_form(ModuleState *state, PyObject *value)
{
    PyObject *items = PyObject_CallMethod(value, "tolist", NULL);
    if (items == NULL) return NULL;
    if (PyList_Check(items)) {
        /* whole numbers of a byte are the bytes they are equal to, whatever the format */
        Py_ssize_t n = PyList_GET_SIZE(items);
        PyObject *bytes = PyBytes_FromStringAndSize(NULL, n);
        int whole = bytes != NULL;
        for (Py_ssize_t i = 0; whole && i < n; i++) {
            PyObject *item = PyList_GET_ITEM(items, i);
            double number = PyLong_Check(item) || PyFloat_Check(item) ? PyFloat_AsDouble(item) : -1.0;
            if (number == -1.0 && PyErr_Occurred()) PyErr_Clear();
            whole = number >= 0 && number <= 255 && floor(number) == number;
            if (whole) PyBytes_AS_STRING(bytes)[i] = (char)(int)number;
        }
        if (whole) {
            Py_DECREF(items);
            return bytes;
        }
        Py_XDECREF(bytes);
        if (PyErr_Occurred()) {
            Py_DECREF(items);
            return NULL;
        }
    }
    PyObject *form = hashable_form(state, items);
    Py_DECREF(items);
    return form;
}

static PyObject *
rebuilt_form(ModuleState *state, PyObject *value, int kind)
{
    switch (kind) {
    case FORM_SEQUENCE: {
        PyObject *snapshot = PySequence_Tuple(value);  /* what another thread changes is not read */
        if (snapshot == NULL) return NULL;
        Py_ssize_t n = PyTuple_GET_SIZE(snapshot);
        PyObject *form = PyTuple_New(n);
        for (Py_ssize_t i = 0; form != NULL && i < n; i++) {
            PyObject *element = hashable_form(state, PyTuple_GET_ITEM(snapshot, i));
            if (element == NULL) Py_CLEAR(form);
            else PyTuple_SET_ITEM(form, i, element);
        }
        Py_DECREF(snapshot);
        return form;
    }
    case FORM_MAPPING: {
        PyObject *source;
        if (PyFrozenDict_Check(value)) {
            source = Py_NewRef(value);
        } else if (PyDict_Check(value)) {
            source = PyDict_Copy(value);
        } else {
            source = PyDict_New();
            if (source != NULL && PyDict_Update(source, value) < 0) Py_CLEAR(source);
        }
        if (source == NULL) return NULL;
        PyObject *form = PyDict_New();
        PyObject *key;
        PyObject *item;
        Py_ssize_t position = 0;
        while (form != NULL && PyDict_Next(source, &position, &key, &item)) {
            PyObject *element = hashable_form(state, item);
            if (element == NULL || PyDict_SetItem(form, key, element) < 0) Py_CLEAR(form);
            Py_XDECREF(element);
        }
        Py_DECREF(source);
        if (form == NULL) return NULL;
        PyObject *frozen = PyObject_CallOneArg((PyObject *)&PyFrozenDict_Type, form);
        Py_DECREF(form);
        return frozen;
    }
    case FORM_SET:
        return PyFrozenSet_New(value);
    case FORM_BYTES:
        return PyBytes_FromObject(value);
    case FORM_VIEW:
        return memoryview_form(state, value);
    default:
        return Py_NewRef(Py_Ellipsis);  /* what has no hash and is no container is left out */
    }
}

static PyObject *
hashable_form_unbounded(ModuleState *state, PyObject *value)
{
    if (hash_is_plain(value)) {
        if (PyObject_Hash(value) != -1) return Py_NewRef(value);  /* hashed by the one hashing the container */
        if (!hash_failure_is_rebuildable(value)) return NULL;
        PyObject *failure = PyErr_GetRaisedException();
        int kind = form_kind(state, value);  /* i.e. a mapping proxy of a dictionary */
        if (kind <= 0) {
            if (kind == 0) PyErr_SetRaisedException(failure);  /* the failure of the value */
            else Py_DECREF(failure);
            return NULL;
        }
        Py_DECREF(failure);
        return rebuilt_form(state, value, kind);
    }
    int kind = form_kind(state, value);
    if (kind < 0) return NULL;
    return rebuilt_form(state, value, kind);
}

/* what stands for a value within a container which is rebuilt */
static PyObject *
hashable_form(ModuleState *state, PyObject *value)
{
    if (Py_EnterRecursiveCall(" while hashing a State")) return NULL;
    PyObject *form = hashable_form_unbounded(state, value);
    Py_LeaveRecursiveCall();
    return form;
}

/*
 * The hash of a value which is the same for every value equal to it - which the hash of the
 * language is not, for the containers which are equal to one of another kind: a `Map` is
 * equal to the dictionary of its items, a `UserDict` or a mapping proxy to it too, a `set` to
 * a `frozenset`, `bytes` to a `bytearray`, a list to a `UserList`, and what they hold the
 * same way. What the language hashes is hashed by it, until it fails to - a tuple holding a
 * list, a mapping proxy of a dictionary - and the rest by the hashable it is rebuilt as. What
 * has no hash of its own - `__hash__` is None - and is no container of those is left out.
 * Any other failure to hash is the failure of it: an instance which is not initialized above
 * all, wherever it is held.
 */
static int
hash_form_unbounded(ModuleState *state, PyObject *value, Py_hash_t *result)
{
    int kind = form_kind(state, value);
    if (kind < 0) return HASH_ERROR;
    if (kind == FORM_NONE) {
        if (Py_TYPE(value)->tp_hash == PyObject_HashNotImplemented) return HASH_LEFT_OUT;
        /* hashed again to fail the same way: a value which is no container, with the failure
         * which is its own */
        Py_hash_t h = PyObject_Hash(value);
        if (h == -1) return HASH_ERROR;
        *result = h;
        return HASH_DONE;
    }
    PyObject *form = rebuilt_form(state, value, kind);
    if (form == NULL) return HASH_ERROR;
    *result = PyObject_Hash(form);
    Py_DECREF(form);
    return *result == -1 ? HASH_ERROR : HASH_DONE;
}

static int
hash_form(ModuleState *state, PyObject *value, Py_hash_t *result)
{
    if (hash_is_plain(value) || PyTuple_Check(value) || PyFrozenDict_Check(value)) {
        /* the most of the values, hashed as they are */
        Py_hash_t h = PyObject_Hash(value);
        if (h != -1) {
            *result = h;
            return HASH_DONE;
        }
        if (!hash_failure_is_rebuildable(value)) return HASH_ERROR;
        PyErr_Clear();  /* what holds what can't be hashed, or is a container of another kind */
    }
    if (Py_EnterRecursiveCall(" while hashing a State")) return HASH_ERROR;
    int r = hash_form_unbounded(state, value, result);
    Py_LeaveRecursiveCall();
    return r;
}

/* the values of an instance are read as they are wherever code runs on them - every one
 * is bound once, for good, so nothing that code does can let go of it */
static Py_hash_t
struct_hash_unbounded(PyObject *self)
{
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject **items = ITEMS(self);
    CompiledPlan *plan = PLAN_OF(Py_TYPE(self));
    PyObject *missing = plan == NULL ? NULL : plan->state->missing;
    ModuleState *state = plan == NULL ? NULL : plan->state;
    Py_uhash_t acc = (Py_uhash_t)(uintptr_t)Py_TYPE(self) * 1000003UL;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = ITEM_LOAD(items, i);
        if (value == NULL) {
            not_initialized(Py_TYPE(self));
            return -1;
        }
        if (value == missing) continue;  /* Missing never takes part */
        Py_hash_t h;
        int r = hash_form(state, value, &h);
        if (r == HASH_ERROR) return -1;
        if (r == HASH_LEFT_OUT) continue;  /* unhashable values are skipped */
        acc = hash_mixed(acc, h);
    }
    return hash_result(acc);
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
            /* only the same concrete class is ever equal - another State is not equal, anything
             * else is left to decide for itself, as `mock.ANY` does */
            CompiledPlan *plan = PLAN_OF(Py_TYPE(a));
            if (plan == NULL || !is_struct(plan->state, b)) Py_RETURN_NOTIMPLEMENTED;
            eq = 0;
        } else {
            Py_ssize_t n = ITEM_COUNT(a);
            for (Py_ssize_t i = 0; i < n && eq; i++) {
                PyObject *x = ITEM_LOAD(ITEMS(a), i), *y = ITEM_LOAD(ITEMS(b), i);
                if (x == NULL || y == NULL) {  /* not initialized - nothing to tell it equal by */
                    not_initialized(Py_TYPE(a));
                    return NULL;
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
    PLAN_SLOT(self) = NULL;
    PyType_Type.tp_dealloc(self);  /* clears the class, its descriptors with it */
    plan_free(plan);
    Py_DECREF(metatype);
}
