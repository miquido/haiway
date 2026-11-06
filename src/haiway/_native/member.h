/* ---------------------------------------------------------------- field */

/*
 * member(cls, index, name) - a read-only member descriptor of the class for the value
 * stored at the index, and the capsule which owns the member definition the descriptor
 * reads. It is the very descriptor `__slots__` creates, so the interpreter specializes
 * the attribute loads of it the same way. The capsule has to be kept as long as the
 * descriptor is - the caller holds it on the class.
 */
typedef struct {
    PyMemberDef member;
    PyObject *name;
} MemberHolder;

static void
member_capsule_destructor(PyObject *capsule)
{
    MemberHolder *holder = PyCapsule_GetPointer(capsule, "haiway.member");
    if (holder == NULL) {
        PyErr_Clear();
        return;
    }
    Py_DECREF(holder->name);
    PyMem_RawFree(holder);
}

static PyObject *
make_member(PyObject *mod, PyObject *args)
{
    PyObject *cls;
    Py_ssize_t index;
    PyObject *name;
    if (!PyArg_ParseTuple(args, "OnU", &cls, &index, &name)) return NULL;
    if (!PyType_Check(cls) || !PyType_IsSubtype((PyTypeObject *)cls, &StructType)) {
        PyErr_SetString(PyExc_TypeError, "member expects a Struct class");
        return NULL;
    }
    const char *utf8 = PyUnicode_AsUTF8(name);
    if (utf8 == NULL) return NULL;
    MemberHolder *holder = PyMem_RawMalloc(sizeof(MemberHolder));
    if (holder == NULL) return PyErr_NoMemory();
    holder->name = Py_NewRef(name);  /* utf8 points into it, so it lives with the holder */
    holder->member.name = utf8;
    holder->member.type = Py_T_OBJECT_EX;
    holder->member.offset = (Py_ssize_t)sizeof(StructObject) + index * (Py_ssize_t)sizeof(PyObject *);
    holder->member.flags = Py_READONLY;
    holder->member.doc = NULL;

    PyObject *capsule = PyCapsule_New(holder, "haiway.member", member_capsule_destructor);
    if (capsule == NULL) {
        Py_DECREF(holder->name);
        PyMem_RawFree(holder);
        return NULL;
    }
    PyObject *descriptor = PyDescr_NewMember((PyTypeObject *)cls, &holder->member);
    if (descriptor == NULL) {
        Py_DECREF(capsule);
        return NULL;
    }
    PyObject *result = PyTuple_Pack(2, descriptor, capsule);
    Py_DECREF(descriptor);
    Py_DECREF(capsule);
    return result;
}
