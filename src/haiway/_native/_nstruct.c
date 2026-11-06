/* The native storage and conversions of haiway.State - see common.h. */

#include "common.h"
#include "member.h"
#include "validate.h"
#include "plan.h"
#include "construct.h"
#include "object.h"
#include "convert.h"
#include "json_write.h"
#include "json_read.h"

static PyMethodDef struct_methods[] = {
    {"__replace__", (PyCFunction)(void (*)(void))struct_replace_fast, METH_FASTCALL | METH_KEYWORDS, NULL},
    {"updating", (PyCFunction)(void (*)(void))struct_replace_fast, METH_FASTCALL | METH_KEYWORDS, NULL},
    {"validate", (PyCFunction)struct_validate, METH_O | METH_CLASS, NULL},
    {"__deepcopy__", struct_deepcopy, METH_O, NULL},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject StructType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "haiway._native._nstruct.Struct",
    .tp_basicsize = sizeof(StructObject),
    .tp_itemsize = sizeof(PyObject *),
    .tp_dealloc = struct_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC,
    .tp_new = (newfunc)struct_new,
    .tp_init = struct_init,
    .tp_traverse = struct_traverse,
    .tp_clear = struct_clear,
    .tp_setattro = struct_setattro,
    .tp_hash = struct_hash,
    .tp_richcompare = struct_richcompare,
    .tp_methods = struct_methods,
};

/* what Python hands the native code to call back - one attribute for each, named */
static int
bind_hook(PyObject *hooks, const char *name, PyObject **slot)
{
    PyObject *value = PyObject_GetAttrString(hooks, name);
    if (value == NULL) return 0;
    Py_XSETREF(*slot, value);
    return 1;
}

static PyObject *
configure(PyObject *mod, PyObject *args)
{
    PyObject *validation, *conversion, *spelling;
    if (!PyArg_ParseTuple(args, "OOO", &validation, &conversion, &spelling)) return NULL;
    if (!bind_hook(validation, "missing", &hook_missing) || !bind_hook(validation, "check", &hook_check)
        || !bind_hook(validation, "report", &hook_report)
        || !bind_hook(validation, "unexpected", &hook_unexpected)
        || !bind_hook(validation, "mapping", &hook_mapping)
        || !bind_hook(conversion, "map", &hook_map) || !bind_hook(conversion, "map_types", &hook_map_types)
        || !bind_hook(conversion, "basic_value", &hook_basic_value)
        || !bind_hook(conversion, "basic_key", &hook_basic_key)
        || !bind_hook(conversion, "recursive", &hook_recursive)
        || !bind_hook(spelling, "uuid_type", &hook_uuid)
        || !bind_hook(spelling, "temporal_types", &hook_temporal)
        || !bind_hook(spelling, "path_type", &hook_path)) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* the numbers of the kinds and of the sources, for Python to spell them with */
static int
add_kinds(PyObject *module)
{
#define ADD_KIND(name) \
    if (PyModule_AddIntConstant(module, #name, name) < 0) return -1
    ADD_KIND(K_ANY);
    ADD_KIND(K_INT);
    ADD_KIND(K_STR);
    ADD_KIND(K_FLOAT);
    ADD_KIND(K_BOOL);
    ADD_KIND(K_CALL);
    ADD_KIND(K_INSTANCE);
    ADD_KIND(K_OPTIONAL);
    ADD_KIND(K_SEQUENCE);
    ADD_KIND(K_ISINSTANCE);
    ADD_KIND(K_MAPPING);
    ADD_KIND(K_TUPLE);
    ADD_KIND(K_LITERAL);
    ADD_KIND(K_SET);
    ADD_KIND(K_UNION);
    ADD_KIND(K_FLOAT_EXACT);
    ADD_KIND(S_NONE);
    ADD_KIND(S_STR);
    ADD_KIND(S_INT);
#undef ADD_KIND
    return 0;
}

static PyMethodDef module_methods[] = {
    {"member", make_member, METH_VARARGS, NULL},
    {"compile_plan", compile_plan, METH_VARARGS, NULL},
    {"to_json", to_json_native, METH_O, NULL},
    {"from_json", from_json_native, METH_VARARGS, NULL},
    {"basic_object", basic_object, METH_VARARGS, NULL},
    {"to_mapping", to_mapping, METH_VARARGS, NULL},
    {"configure", configure, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT, "_nstruct", NULL, -1, module_methods, NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit__nstruct(void)
{
    if (PyType_Ready(&StructType) < 0) return NULL;
    PyObject *m = PyModule_Create(&moduledef);
    if (m == NULL) return NULL;
    str_cplan = PyUnicode_InternFromString("__cplan__");
    str_isoformat = PyUnicode_InternFromString("isoformat");
    str_as_posix = PyUnicode_InternFromString("as_posix");
    nocache = PyObject_CallNoArgs((PyObject *)&PyBaseObject_Type);
    if (str_cplan == NULL || str_isoformat == NULL || str_as_posix == NULL || nocache == NULL ||
        PyModule_AddObjectRef(m, "NOCACHE", nocache) < 0 ||
        PyModule_AddObjectRef(m, "Struct", (PyObject *)&StructType) < 0 ||
        add_kinds(m) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    return m;
}
