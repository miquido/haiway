/* The native storage and conversions of haiway.State - see common.h. */

#include "common.h"
#include "spec.h"
#include "plan.h"
#include "construct.h"
#include "object.h"
#include "convert.h"
#include "json_write.h"
#include "json_read.h"

static PyMethodDef struct_methods[] = {
    {"__replace__", (PyCFunction)(void (*)(void))struct_replace_fast, METH_FASTCALL | METH_KEYWORDS, NULL},
    {"updating", (PyCFunction)(void (*)(void))struct_replace_fast, METH_FASTCALL | METH_KEYWORDS, NULL},
    {"__deepcopy__", struct_deepcopy, METH_O, NULL},
    {NULL, NULL, 0, NULL}
};

/* what a class validating its input adds to the storage - a plain record has no `validate` */
static PyMethodDef validated_methods[] = {
    {"validate", (PyCFunction)struct_validate, METH_O | METH_CLASS, NULL},
    {NULL, NULL, 0, NULL}
};

static PyType_Slot validated_slots[] = {
    {Py_tp_traverse, struct_traverse},
    {Py_tp_clear, struct_clear},
    {Py_tp_methods, validated_methods},
    {0, NULL}
};

static PyType_Spec validated_spec = {
    .name = "haiway._native._nstruct.Validated",
    .basicsize = sizeof(PyObject),
    .itemsize = 0,
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = validated_slots,
};

static PyType_Slot struct_slots[] = {
    {Py_tp_dealloc, struct_dealloc},
    {Py_tp_traverse, struct_traverse},
    {Py_tp_clear, struct_clear},
    {Py_tp_new, struct_new},
    {Py_tp_init, struct_init},
    {Py_tp_setattro, struct_setattro},
    {Py_tp_hash, struct_hash},
    {Py_tp_richcompare, struct_richcompare},
    {Py_tp_methods, struct_methods},
    {0, NULL}
};

static PyType_Spec struct_spec = {
    .name = "haiway._native._nstruct.Struct",
    .basicsize = sizeof(PyObject),
    .itemsize = 0,
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = struct_slots,
};

static PyType_Slot structmeta_slots[] = {
    {Py_tp_dealloc, structmeta_dealloc},
    {Py_tp_traverse, structmeta_traverse},
    {Py_tp_clear, structmeta_clear},
    {0, NULL}
};

static PyType_Spec structmeta_spec = {
    .name = "haiway._native._nstruct.StructMeta",
    .basicsize = sizeof(StructMetaObject),
    .itemsize = 0,  /* the one of `type` - the slot members of a class follow the object */
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = structmeta_slots,
};

static PyType_Slot spec_slots[] = {
    {Py_tp_dealloc, spec_object_dealloc},
    {Py_tp_traverse, spec_object_traverse},
    {Py_tp_clear, spec_object_clear},
    {0, NULL}
};

static PyType_Spec spec_spec = {
    .name = "haiway._native._nstruct.Spec",
    .basicsize = sizeof(SpecObject),
    .itemsize = 0,
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = spec_slots,
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

/* configure(validation, conversion, types) - once, before any class is given a plan */
static PyObject *
configure(PyObject *mod, PyObject *args)
{
    ModuleState *state = module_state(mod);
    PyObject *validation, *conversion, *types;
    if (!PyArg_ParseTuple(args, "OOO", &validation, &conversion, &types)) return NULL;
    if (state->missing != NULL) {
        PyErr_SetString(PyExc_RuntimeError, "the native State is configured already");
        return NULL;
    }
    if (!bind_hook(validation, "check", &state->check) || !bind_hook(validation, "report", &state->report)
        || !bind_hook(validation, "unexpected", &state->unexpected)
        || !bind_hook(validation, "describe", &state->describe)
        || !bind_hook(validation, "accepts", &state->accepts)
        || !bind_hook(validation, "enum_values", &state->enum_values)
        || !bind_hook(validation, "literal_values", &state->literal_values)
        || !bind_hook(validation, "b64decode", &state->b64decode)
        || !bind_hook(validation, "parse_duration", &state->parse_duration)
        || !bind_hook(validation, "exception_group", &state->exception_group)
        || !bind_hook(conversion, "map", &state->map)
        || !bind_hook(conversion, "basic_value", &state->basic_value)
        || !bind_hook(conversion, "basic_key", &state->basic_key)
        || !bind_hook(conversion, "recursive", &state->recursive)
        || !bind_hook(types, "mapping", &state->mapping) || !bind_hook(types, "sequence", &state->sequence)
        || !bind_hook(types, "set", &state->set) || !bind_hook(types, "iterable", &state->iterable)
        || !bind_hook(types, "uuid", &state->uuid) || !bind_hook(types, "safe_uuid", &state->safe_uuid)
        || !bind_hook(types, "datetime", &state->datetime)
        || !bind_hook(types, "date", &state->date) || !bind_hook(types, "time", &state->time)
        || !bind_hook(types, "timedelta", &state->timedelta) || !bind_hook(types, "path", &state->path)
        || !bind_hook(types, "pathlike", &state->pathlike) || !bind_hook(types, "meta", &state->meta)
        || !bind_hook(types, "partial", &state->partial)) {
        return NULL;
    }
    PyObject *temporal = PyTuple_Pack(3, state->datetime, state->date, state->time);
    if (temporal == NULL) return NULL;
    Py_XSETREF(state->temporal, temporal);
    /* last - it tells the module is configured */
    if (!bind_hook(validation, "missing", &state->missing)) return NULL;
    Py_RETURN_NONE;
}

/* the numbers of the kinds of a spec, for Python to spell them with */
static int
add_kinds(PyObject *module)
{
#define ADD_KIND(name) \
    if (PyModule_AddIntConstant(module, #name, name) < 0) return -1
    ADD_KIND(SPEC_ANY);
    ADD_KIND(SPEC_NONE);
    ADD_KIND(SPEC_MISSING);
    ADD_KIND(SPEC_LITERAL);
    ADD_KIND(SPEC_BOOL);
    ADD_KIND(SPEC_INT);
    ADD_KIND(SPEC_FLOAT);
    ADD_KIND(SPEC_STR);
    ADD_KIND(SPEC_BYTES);
    ADD_KIND(SPEC_UUID);
    ADD_KIND(SPEC_DATETIME);
    ADD_KIND(SPEC_DATE);
    ADD_KIND(SPEC_TIME);
    ADD_KIND(SPEC_TIMEDELTA);
    ADD_KIND(SPEC_PATH);
    ADD_KIND(SPEC_STR_ENUM);
    ADD_KIND(SPEC_INT_ENUM);
    ADD_KIND(SPEC_TUPLE);
    ADD_KIND(SPEC_SEQUENCE);
    ADD_KIND(SPEC_SET);
    ADD_KIND(SPEC_MAPPING);
    ADD_KIND(SPEC_META);
    ADD_KIND(SPEC_TYPED_DICT);
    ADD_KIND(SPEC_STATE);
    ADD_KIND(SPEC_CUSTOM_STATE);
    ADD_KIND(SPEC_CUSTOM);
    ADD_KIND(SPEC_FUNCTION);
    ADD_KIND(SPEC_UNION);
    ADD_KIND(SPEC_VALIDABLE);
    ADD_KIND(SPEC_ALIAS);
#undef ADD_KIND
    return 0;
}

static PyMethodDef module_methods[] = {
    {"compile_spec", compile_spec, METH_O, NULL},
    {"validate", (PyCFunction)(void (*)(void))validate_native, METH_FASTCALL, NULL},
    {"check", (PyCFunction)(void (*)(void))check_native, METH_FASTCALL, NULL},
    {"compile_plan", compile_plan, METH_VARARGS, NULL},
    {"to_json", to_json_native, METH_O, NULL},
    {"from_json", from_json_native, METH_VARARGS, NULL},
    {"basic_object", basic_object, METH_VARARGS, NULL},
    {"to_mapping", to_mapping, METH_VARARGS, NULL},
    {"configure", configure, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}
};

static int
intern(PyObject **slot, const char *text)
{
    *slot = PyUnicode_InternFromString(text);
    return *slot == NULL ? -1 : 0;
}

static int
module_exec(PyObject *module)
{
    ModuleState *state = module_state(module);
    state->meta_type = (PyTypeObject *)PyType_FromMetaclass(NULL, module, &structmeta_spec, (PyObject *)&PyType_Type);
    if (state->meta_type == NULL) return -1;
    state->struct_type = (PyTypeObject *)PyType_FromMetaclass(state->meta_type, module, &struct_spec, NULL);
    if (state->struct_type == NULL) return -1;
    state->validated_type = (PyTypeObject *)PyType_FromMetaclass(
        state->meta_type, module, &validated_spec, (PyObject *)state->struct_type);
    if (state->validated_type == NULL) return -1;
    state->spec_type = (PyTypeObject *)PyType_FromMetaclass(NULL, module, &spec_spec, NULL);
    if (state->spec_type == NULL) return -1;

    if (intern(&state->str_isoformat, "isoformat") < 0 || intern(&state->str_as_posix, "as_posix") < 0
        || intern(&state->str_type_name, "type_name") < 0
        || intern(&state->str_lower, "lower") < 0 || intern(&state->str_fromisoformat, "fromisoformat") < 0
        || intern(&state->str_validate, "validate") < 0 || intern(&state->str_int, "int") < 0
        || intern(&state->str_code, "__code__") < 0 || intern(&state->str_defaults, "__defaults__") < 0
        || intern(&state->str_kwdefaults, "__kwdefaults__") < 0 || intern(&state->str_call, "__call__") < 0
        || intern(&state->str_func, "func") < 0 || intern(&state->str_args, "args") < 0
        || intern(&state->str_keywords, "keywords") < 0) {
        return -1;
    }
    /* the C API of datetime - what values are spelled from natively, see convert.h */
    PyDateTime_IMPORT;
    if (PyDateTimeAPI == NULL) return -1;
    PyObject *copy = PyImport_ImportModule("copy");
    if (copy == NULL) return -1;
    state->deepcopy = PyObject_GetAttrString(copy, "deepcopy");
    Py_DECREF(copy);
    if (state->deepcopy == NULL) return -1;

    if (PyModule_AddObjectRef(module, "StructMeta", (PyObject *)state->meta_type) < 0
        || PyModule_AddObjectRef(module, "Struct", (PyObject *)state->struct_type) < 0
        || PyModule_AddObjectRef(module, "Validated", (PyObject *)state->validated_type) < 0
        || PyModule_AddObjectRef(module, "Spec", (PyObject *)state->spec_type) < 0
        || add_kinds(module) < 0) {
        return -1;
    }
    return 0;
}

#define FOR_EACH_STATE_OBJECT(V) \
    V(meta_type) V(struct_type) V(validated_type) V(spec_type) \
    V(missing) V(check) V(report) V(unexpected) V(describe) V(accepts) V(enum_values) V(literal_values) \
    V(b64decode) V(parse_duration) V(exception_group) \
    V(map) V(basic_value) V(basic_key) V(recursive) \
    V(mapping) V(sequence) V(set) V(iterable) V(uuid) V(safe_uuid) V(datetime) V(date) V(time) V(timedelta) \
    V(temporal) V(path) V(pathlike) V(meta) V(partial) \
    V(str_isoformat) V(str_as_posix) V(str_type_name) V(str_lower) \
    V(str_fromisoformat) V(str_validate) V(str_int) V(str_code) V(str_defaults) V(str_kwdefaults) \
    V(str_call) V(str_func) V(str_args) V(str_keywords) \
    V(deepcopy)

static int
module_traverse(PyObject *module, visitproc visit, void *arg)
{
    ModuleState *state = module_state(module);
#define VISIT(name) Py_VISIT(state->name);
    FOR_EACH_STATE_OBJECT(VISIT)
#undef VISIT
    return 0;
}

static int
module_clear(PyObject *module)
{
    ModuleState *state = module_state(module);
#define CLEAR(name) Py_CLEAR(state->name);
    FOR_EACH_STATE_OBJECT(CLEAR)
#undef CLEAR
    return 0;
}

static void
module_free(void *module)
{
    module_clear((PyObject *)module);
}

static PyModuleDef_Slot module_slots[] = {
    {Py_mod_exec, module_exec},
    {Py_mod_multiple_interpreters, Py_MOD_MULTIPLE_INTERPRETERS_NOT_SUPPORTED},
    /* the code is written to run without the GIL (see common.h), but the GIL is still
     * required - Py_MOD_GIL_NOT_USED is the switch, once the free-threaded build is tested */
    {Py_mod_gil, Py_MOD_GIL_USED},
    {0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_nstruct",
    .m_size = sizeof(ModuleState),
    .m_methods = module_methods,
    .m_slots = module_slots,
    .m_traverse = module_traverse,
    .m_clear = module_clear,
    .m_free = module_free,
};

PyMODINIT_FUNC
PyInit__nstruct(void)
{
    return PyModuleDef_Init(&moduledef);
}
