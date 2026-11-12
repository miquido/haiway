#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <datetime.h>

/*
 * Native storage for haiway.State.
 *
 * Instances are fixed-size objects holding their field values inline, right after
 * the object header - how many is a property of their class, set when the class is
 * given its plan. There is no __dict__, no __weakref__ and no setter anywhere, so
 * the only way to bind a field is through the constructors below - which makes the
 * immutability real, rather than a __setattr__ convention.
 *
 * Everything that is decided once per class (annotation resolution, generics,
 * schemas) stays in Python. The metaclass hands the native code a "plan" of the class
 * once, which `compile_plan` turns into the data every instance is built and read from,
 * kept on the class itself: the classes are instances of `StructMeta`, a metaclass with
 * one field of its own, the pointer to that plan - see plan.h.
 *
 * The module still requires the GIL, but it is written to run without it, so that
 * supporting the free-threaded interpreter is a declaration away (`Py_mod_gil`): it keeps
 * its state per module, never holds a borrowed reference into a container another thread
 * could change, validates snapshots of what it is given, and reads the plan and the values
 * of an instance freely, since both are written once before anything else can see them.
 * New code has to keep to that. The file is one translation unit made of the headers
 * below, in order.
 */

/* what Python hands the native code to call back, and the types the module makes */
typedef struct {
    PyTypeObject *meta_type;     /* StructMeta */
    PyTypeObject *struct_type;   /* Struct - the storage of a State */
    PyTypeObject *spec_type;     /* Spec - a compiled description of an annotation */
    /* validation */
    PyObject *missing;           /* Missing */
    PyObject *check;             /* (cls, mapping) -> None, raises on unexpected / duplicate keys */
    PyObject *report;            /* (segment, exc) -> NoReturn, the failure at a position */
    PyObject *unexpected;        /* (cls, key) -> NoReturn, a key of a mapping which is not an attribute */
    PyObject *describe;          /* (annotation) -> the description of a spec */
    PyObject *parameters;        /* (callable) -> ((name, kind, defaulted), ...), None without a signature */
    PyObject *enum_values;       /* (enum, names) -> the values (or the names) of its members, spelled */
    PyObject *literal_values;    /* (values) -> the values of a literal, spelled */
    PyObject *b64decode;         /* (str) -> bytes, strictly */
    PyObject *parse_duration;    /* (str) -> timedelta */
    PyObject *exception_group;   /* builtins.ExceptionGroup */
    /* conversion */
    PyObject *map;               /* the Map type */
    PyObject *basic_value;       /* (value, strict, path) -> basic value, what is not handled here */
    PyObject *basic_key;         /* (key, strict, path) -> str key */
    PyObject *recursive;         /* (value) -> mapping, what is not handled here */
    /* types */
    PyObject *mapping;           /* collections.abc.Mapping */
    PyObject *sequence;          /* collections.abc.Sequence */
    PyObject *set;               /* collections.abc.Set */
    PyObject *iterable;          /* collections.abc.Iterable */
    PyObject *uuid;              /* uuid.UUID */
    PyObject *safe_uuid;         /* uuid.SafeUUID - what a UUID holds besides its number */
    PyObject *datetime;
    PyObject *date;
    PyObject *time;
    PyObject *timedelta;
    PyObject *temporal;          /* (datetime, date, time) */
    PyObject *path;              /* pathlib.Path */
    PyObject *pathlike;          /* os.PathLike */
    PyObject *meta;              /* the Meta type */
    PyObject *partial;           /* functools.partial */
    PyObject *placeholder;       /* functools.Placeholder */
    /* the rest */
    PyObject *str_isoformat;
    PyObject *str_as_posix;
    PyObject *str_type_name;
    PyObject *str_lower;
    PyObject *str_fromisoformat;
    PyObject *str_validate;
    PyObject *str_int;
    PyObject *str_code;
    PyObject *str_defaults;
    PyObject *str_kwdefaults;
    PyObject *str_call;
    PyObject *str_func;
    PyObject *str_args;
    PyObject *str_keywords;
    PyObject *deepcopy;          /* copy.deepcopy */
} ModuleState;

struct CompiledPlan;
struct Spec;

/* a compiled description of an annotation - see spec.h */
typedef struct {
    PyObject_HEAD
    struct Spec *spec;
} SpecObject;

/* a class of the module - a type object with the plan of its instances */
typedef struct {
    PyHeapTypeObject base;
    struct CompiledPlan *plan;  /* NULL until the class is given one */
} StructMetaObject;

#define PLAN_OF(type) (((StructMetaObject *)(type))->plan)

/* the values of an instance follow the object header, as many as its class holds */
#define ITEMS(o) ((PyObject **)(((char *)(o)) + sizeof(PyObject)))
#define ITEM_COUNT(o) \
    ((Py_TYPE(o)->tp_basicsize - (Py_ssize_t)sizeof(PyObject)) / (Py_ssize_t)sizeof(PyObject *))

static inline ModuleState *
module_state(PyObject *module)
{
    return (ModuleState *)PyModule_GetState(module);
}

static inline int
is_struct(ModuleState *state, PyObject *value)
{
    return PyType_IsSubtype(Py_TYPE(value), state->struct_type);
}
