#define PY_SSIZE_T_CLEAN
#include <Python.h>

/*
 * Native storage for haiway.State.
 *
 * Instances are var-sized objects holding their field values inline, right after
 * the object header. There is no __dict__, no __weakref__ and no setter anywhere,
 * so the only way to bind a field is through the constructors below - which makes
 * the immutability real, rather than a __setattr__ convention.
 *
 * Everything that is decided once per class (annotation resolution, generics,
 * schemas) stays in Python. The metaclass hands the native code a "plan" of the class
 * once, which `compile_plan` turns into the data every instance is built and read from
 * - see plan.h. The file is one translation unit made of the headers below, in order.
 */

/*
 * What of an input an annotation accepts as it is, so no call of Python is needed to
 * validate it. The numbers are exported by the module (`K_*`), where Python reads
 * them - nothing else spells them.
 */
enum {
    K_ANY = 0,
    K_INT,
    K_STR,
    K_FLOAT,
    K_BOOL,
    K_CALL,  /* nothing - the annotation of Python code validates */
    K_INSTANCE,
    K_OPTIONAL,
    K_SEQUENCE,
    K_ISINSTANCE,
    K_MAPPING,
    K_TUPLE,
    K_LITERAL,
    K_SET,
    K_UNION,
    K_FLOAT_EXACT,
};

/* what of an input an instance of a type is read from, besides being one */
enum {
    S_NONE = 0,
    S_STR,
    S_INT,
};

typedef struct {
    PyObject_VAR_HEAD
    /* PyObject *items[]; follows */
} StructObject;

#define ITEMS(o) ((PyObject **)(((char *)(o)) + sizeof(StructObject)))

static PyTypeObject StructType;
static PyObject *nocache;
static PyObject *hook_missing;  /* MISSING */
static PyObject *hook_check;    /* (cls, mapping) -> None, raises on unexpected / duplicate keys */
static PyObject *hook_report;   /* (path, exc) -> NoReturn */
static PyObject *hook_mapping;        /* collections.abc.Mapping */
static PyObject *hook_uuid;            /* uuid.UUID */
static PyObject *hook_temporal;        /* (datetime, date, time) */
static PyObject *hook_path;            /* pathlib.Path */
static PyObject *str_isoformat;
static PyObject *str_as_posix;
static PyObject *hook_unexpected;     /* (cls, key) -> NoReturn, a key of a mapping which is not an attribute */
static PyObject *hook_map;      /* dict -> Map */
static PyObject *hook_map_types;      /* the mapping types walked as plain dictionaries */
static PyObject *hook_basic_value;    /* (value, strict, path) -> basic value, what is not handled here */
static PyObject *hook_basic_key;      /* (key, strict, path) -> str key */
static PyObject *hook_recursive;      /* (value) -> mapping, what is not handled here */
