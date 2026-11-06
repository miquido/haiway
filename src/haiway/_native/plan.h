/* ----------------------------------------------------------------- plan */

/*
 * The plan of a class, as C data. It is the only description of the attributes of a
 * class the native code has: what they are called, what validates them, what they
 * default to. The names are compared by identity (they are interned, as are the keyword
 * names of a call), and no dictionary of the keyword arguments is built at all for the
 * usual call. Whatever does not fit - a name which is not interned, one which is not an
 * attribute, both an attribute and its alias, a lot of arguments - takes the general
 * path, which is the one telling what is wrong with it.
 */
#define FAST_MAX_FIELDS 64
#define FAST_MAX_KEYWORDS 32

typedef struct {
    PyObject *name;
    PyObject *alias;  /* NULL when there is none */
    long kind;
    PyObject *validate;
    PyObject *default_value;
    PyObject *cached;  /* NULL when the default is not a constant */
    PyObject *arg;
} FieldPlan;

typedef struct {
    PyObject *owner;    /* the plan tuple - what the pointers above point into */
    PyObject *allowed;  /* every name and alias, as a frozenset */
    PyObject *keys;     /* every name and alias, with the position of its attribute */
    Py_ssize_t count;
    FieldPlan fields[1];
} CompiledPlan;

static PyObject *str_cplan;
#define CPLAN_CAPSULE "haiway.plan"

static void
compiled_plan_destructor(PyObject *capsule)
{
    CompiledPlan *plan = PyCapsule_GetPointer(capsule, CPLAN_CAPSULE);
    if (plan == NULL) {
        PyErr_Clear();
        return;
    }
    Py_DECREF(plan->owner);
    Py_DECREF(plan->allowed);
    Py_DECREF(plan->keys);
    PyMem_RawFree(plan);
}

/* the compiled plan of a class which has one of its own, NULL otherwise */
static inline CompiledPlan *
compiled_plan_of(PyTypeObject *type)
{
    PyObject *dict = PyType_GetDict(type);
    if (dict == NULL) return NULL;
    PyObject *capsule = PyDict_GetItemWithError(dict, str_cplan);
    Py_DECREF(dict);
    if (capsule == NULL) {
        PyErr_Clear();
        return NULL;
    }
    return PyCapsule_GetPointer(capsule, CPLAN_CAPSULE);
}

static inline PyObject *
validate_field(FieldPlan *field, PyObject *value)
{
    PyObject *out = NULL;
    int r = fast_validate(field->kind, field->arg, value, &out);
    if (r > 0) return out;
    if (r < 0) return NULL;
    return PyObject_CallOneArg(field->validate, value);
}

/* the key an attribute has in a mapping or in json - its alias when it has one */
static inline PyObject *
field_key(FieldPlan *field)
{
    return field->alias != NULL ? field->alias : field->name;
}

/* the compiled plan of a class, or the error of there being none */
static inline CompiledPlan *
plan_or_error(PyTypeObject *type)
{
    CompiledPlan *plan = compiled_plan_of(type);
    if (plan == NULL) PyErr_Format(PyExc_TypeError, "%s was never given a plan", type->tp_name);
    return plan;
}

static PyObject *struct_vectorcall(PyObject *callable, PyObject *const *args, size_t nargsf, PyObject *kwnames);

/* the entries of the plan handed over by Python, one for each attribute */
#define E_NAME(e) PyTuple_GET_ITEM((e), 0)
#define E_ALIAS(e) PyTuple_GET_ITEM((e), 1)
#define E_KIND(e) PyTuple_GET_ITEM((e), 2)
#define E_VALIDATE(e) PyTuple_GET_ITEM((e), 3)
#define E_DEFAULT(e) PyTuple_GET_ITEM((e), 4)
#define E_CACHED(e) PyTuple_GET_ITEM((e), 5)
#define E_ARG(e) PyTuple_GET_ITEM((e), 6)

/*
 * compile_plan(cls, plan) - the plan of the class as C data, kept on the class. `plan` is a
 * tuple with an entry for each attribute:
 *
 *   (name, alias, kind, validate, default, cached, arg)
 *
 * `kind` and `arg` describe the input which needs no validation call, `cached` is the
 * validated default when it is a constant of an immutable type and `nocache` otherwise.
 * The call of the class goes through it when the class does not take over its
 * construction.
 */
static PyObject *
compile_plan(PyObject *mod, PyObject *args)
{
    PyObject *cls;
    PyObject *plan;
    if (!PyArg_ParseTuple(args, "OO!", &cls, &PyTuple_Type, &plan)) return NULL;
    if (!PyType_Check(cls) || !PyType_IsSubtype((PyTypeObject *)cls, &StructType)) {
        PyErr_SetString(PyExc_TypeError, "compile expects a Struct class");
        return NULL;
    }
    Py_ssize_t n = PyTuple_GET_SIZE(plan);
    CompiledPlan *compiled = PyMem_RawMalloc(sizeof(CompiledPlan) + (n > 0 ? n - 1 : 0) * sizeof(FieldPlan));
    if (compiled == NULL) return PyErr_NoMemory();
    compiled->owner = Py_NewRef(plan);
    compiled->count = n;
    compiled->allowed = PyFrozenSet_New(NULL);
    compiled->keys = PyDict_New();
    if (compiled->allowed == NULL || compiled->keys == NULL) goto fail;

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *entry = PyTuple_GET_ITEM(plan, i);
        FieldPlan *field = &compiled->fields[i];
        field->name = E_NAME(entry);
        field->alias = E_ALIAS(entry) == Py_None ? NULL : E_ALIAS(entry);
        field->kind = PyLong_AsLong(E_KIND(entry));
        field->validate = E_VALIDATE(entry);
        field->default_value = E_DEFAULT(entry);
        field->cached = E_CACHED(entry) == nocache ? NULL : E_CACHED(entry);
        field->arg = E_ARG(entry);
        PyObject *position = PyLong_FromSsize_t(i);
        if (position == NULL) goto fail;
        int failed = PySet_Add(compiled->allowed, field->name) < 0
            || PyDict_SetItem(compiled->keys, field->name, position) < 0
            || (field->alias != NULL
                && (PySet_Add(compiled->allowed, field->alias) < 0
                    || PyDict_SetItem(compiled->keys, field->alias, position) < 0));
        Py_DECREF(position);
        if (failed) goto fail;
    }
    PyObject *capsule = PyCapsule_New(compiled, CPLAN_CAPSULE, compiled_plan_destructor);
    if (capsule == NULL) goto fail;
    int failed = PyObject_SetAttr(cls, str_cplan, capsule);
    Py_DECREF(capsule);
    if (failed < 0) return NULL;  /* the capsule owns the plan now */

    /* a class taking over its construction is called the usual way */
    PyTypeObject *type = (PyTypeObject *)cls;
    if (type->tp_new == StructType.tp_new && type->tp_init == StructType.tp_init && n <= FAST_MAX_FIELDS) {
        type->tp_vectorcall = struct_vectorcall;
    }
    Py_RETURN_NONE;

fail:
    Py_XDECREF(compiled->allowed);
    Py_XDECREF(compiled->keys);
    Py_DECREF(compiled->owner);
    PyMem_RawFree(compiled);
    return NULL;
}

#undef E_NAME
#undef E_ALIAS
#undef E_KIND
#undef E_VALIDATE
#undef E_DEFAULT
#undef E_CACHED
#undef E_ARG
