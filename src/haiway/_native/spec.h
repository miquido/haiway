/* ------------------------------------------------------------------ spec */

/*
 * What an annotation accepts, as C data. Python resolves the annotations of a class and
 * describes each as a tuple - (kind, annotation, base, validating, verifying, data,
 * children) - which `compile_spec` turns into a tree of these. Every built-in kind
 * validates here, with the messages the Python annotations have always raised with;
 * what runs Python is the code of the user (a Validator, a Verifier, a class validating
 * itself) and the conversions of the standard library.
 *
 * An alias stands for a type which may refer back to it, so it is described lazily: its
 * target is compiled the first time it validates. The numbers of the kinds are exported
 * by the module (`SPEC_*`), where Python reads them - nothing else spells them.
 */
enum {
    SPEC_ANY = 0,
    SPEC_NONE,
    SPEC_MISSING,
    SPEC_LITERAL,      /* data: the tuple of the values */
    SPEC_BOOL,
    SPEC_INT,
    SPEC_FLOAT,
    SPEC_STR,
    SPEC_BYTES,
    SPEC_UUID,
    SPEC_DATETIME,
    SPEC_DATE,
    SPEC_TIME,
    SPEC_TIMEDELTA,
    SPEC_PATH,
    SPEC_STR_ENUM,     /* base: the enum */
    SPEC_INT_ENUM,     /* base: the enum */
    SPEC_TUPLE,        /* children: one per position */
    SPEC_SEQUENCE,     /* children: the element */
    SPEC_SET,          /* children: the element */
    SPEC_MAPPING,      /* children: the key, the value */
    SPEC_META,
    SPEC_TYPED_DICT,   /* base: the TypedDict, data: ((key, required), ...), children: one per key */
    SPEC_STATE,        /* base: a State class validating the usual way */
    SPEC_CUSTOM_STATE, /* base: a State class with a validate of its own */
    SPEC_CUSTOM,       /* base: any other class */
    SPEC_FUNCTION,
    SPEC_UNION,        /* children: the alternatives */
    SPEC_VALIDABLE,    /* children: the attribute, validating: what runs on the raw value first */
    SPEC_ALIAS,        /* annotation: the alias, resolved on first use into children[0] */
};

typedef struct Spec {
    int kind;
    int pure;               /* no code of the user runs, and the result is immutable */
    int nested;             /* validates other specs, or instances - guarded against recursion */
    PyObject *annotation;   /* the annotation - its type_name and base word the errors */
    PyObject *base;
    PyObject *validating;   /* NULL for none */
    PyObject *verifying;    /* NULL for none */
    PyObject *data;
    Py_ssize_t count;
    struct Spec **children;
} Spec;

static void
spec_free(Spec *spec)
{
    if (spec == NULL) return;
    Py_XDECREF(spec->annotation);
    Py_XDECREF(spec->base);
    Py_XDECREF(spec->validating);
    Py_XDECREF(spec->verifying);
    Py_XDECREF(spec->data);
    if (spec->children != NULL) {
        for (Py_ssize_t i = 0; i < spec->count; i++) spec_free(spec->children[i]);
        PyMem_RawFree(spec->children);
    }
    PyMem_RawFree(spec);
}

static int
spec_traverse(Spec *spec, visitproc visit, void *arg)
{
    if (spec == NULL) return 0;
    Py_VISIT(spec->annotation);
    Py_VISIT(spec->base);
    Py_VISIT(spec->validating);
    Py_VISIT(spec->verifying);
    Py_VISIT(spec->data);
    for (Py_ssize_t i = 0; i < spec->count; i++) {
        int r = spec_traverse(spec->children[i], visit, arg);
        if (r != 0) return r;
    }
    return 0;
}

/* the entries of a description handed over by Python */
#define D_KIND(e) PyTuple_GET_ITEM((e), 0)
#define D_ANNOTATION(e) PyTuple_GET_ITEM((e), 1)
#define D_BASE(e) PyTuple_GET_ITEM((e), 2)
#define D_VALIDATING(e) PyTuple_GET_ITEM((e), 3)
#define D_VERIFYING(e) PyTuple_GET_ITEM((e), 4)
#define D_DATA(e) PyTuple_GET_ITEM((e), 5)
#define D_CHILDREN(e) PyTuple_GET_ITEM((e), 6)

static Spec *
spec_compile(PyObject *description)
{
    if (!PyTuple_Check(description) || PyTuple_GET_SIZE(description) != 7 || !PyLong_Check(D_KIND(description))
        || !PyTuple_Check(D_CHILDREN(description))) {
        PyErr_SetString(PyExc_TypeError, "a spec is described by (kind, annotation, base, validating, verifying, data, children)");
        return NULL;
    }
    Spec *spec = PyMem_RawCalloc(1, sizeof(Spec));
    if (spec == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    spec->kind = (int)PyLong_AsLong(D_KIND(description));
    spec->annotation = Py_NewRef(D_ANNOTATION(description));
    spec->base = D_BASE(description) == Py_None ? NULL : Py_NewRef(D_BASE(description));
    spec->validating = D_VALIDATING(description) == Py_None ? NULL : Py_NewRef(D_VALIDATING(description));
    spec->verifying = D_VERIFYING(description) == Py_None ? NULL : Py_NewRef(D_VERIFYING(description));
    spec->data = D_DATA(description) == Py_None ? NULL : Py_NewRef(D_DATA(description));
    PyObject *children = D_CHILDREN(description);
    Py_ssize_t n = PyTuple_GET_SIZE(children);
    /* an alias keeps room for its target, which is compiled when it is first used */
    Py_ssize_t room = spec->kind == SPEC_ALIAS ? 1 : n;
    if (room > 0) {
        spec->children = PyMem_RawCalloc((size_t)room, sizeof(Spec *));
        if (spec->children == NULL) {
            PyErr_NoMemory();
            spec_free(spec);
            return NULL;
        }
    }
    spec->count = n;
    spec->pure = spec->validating == NULL && spec->verifying == NULL && spec->kind != SPEC_CUSTOM
        && spec->kind != SPEC_CUSTOM_STATE && spec->kind != SPEC_ALIAS;
    spec->nested = n > 0 || spec->kind == SPEC_ALIAS || spec->kind == SPEC_STATE || spec->kind == SPEC_CUSTOM_STATE
        || spec->kind == SPEC_CUSTOM || spec->kind == SPEC_META;
    for (Py_ssize_t i = 0; i < n; i++) {
        spec->children[i] = spec_compile(PyTuple_GET_ITEM(children, i));
        if (spec->children[i] == NULL) {
            spec_free(spec);
            return NULL;
        }
        if (!spec->children[i]->pure) spec->pure = 0;
    }
    return spec;
}

#undef D_KIND
#undef D_ANNOTATION
#undef D_BASE
#undef D_VALIDATING
#undef D_VERIFYING
#undef D_DATA
#undef D_CHILDREN

/* the object owning a spec - what the collector sees the annotations through */
static int
spec_object_traverse(PyObject *self, visitproc visit, void *arg)
{
    Py_VISIT(Py_TYPE(self));
    return spec_traverse(((SpecObject *)self)->spec, visit, arg);
}

static int
spec_object_clear(PyObject *self)
{
    Spec *spec = ((SpecObject *)self)->spec;
    ((SpecObject *)self)->spec = NULL;
    spec_free(spec);
    return 0;
}

static void
spec_object_dealloc(PyObject *self)
{
    PyTypeObject *tp = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    spec_object_clear(self);
    tp->tp_free(self);
    Py_DECREF(tp);
}

/* compile_spec(description) - a spec as C data, owned by the object returned */
static PyObject *
compile_spec(PyObject *mod, PyObject *description)
{
    ModuleState *state = module_state(mod);
    Spec *spec = spec_compile(description);
    if (spec == NULL) return NULL;
    PyObject *self = state->spec_type->tp_alloc(state->spec_type, 0);
    if (self == NULL) {
        spec_free(spec);
        return NULL;
    }
    ((SpecObject *)self)->spec = spec;
    return self;
}

static inline Spec *
spec_of(ModuleState *state, PyObject *object)
{
    if (!Py_IS_TYPE(object, state->spec_type) || ((SpecObject *)object)->spec == NULL) {
        PyErr_SetString(PyExc_TypeError, "expected a compiled spec");
        return NULL;
    }
    return ((SpecObject *)object)->spec;
}

/* ------------------------------------------------------------ messages */

static PyObject *
type_error(const char *expected, PyObject *value)
{
    PyErr_Format(PyExc_TypeError, "'%s' is not matching expected type of '%s'", Py_TYPE(value)->tp_name, expected);
    return NULL;
}

/* the same, with the expected type spelled the way the class is */
static PyObject *
type_error_of(PyObject *expected, PyObject *value)
{
    PyErr_Format(PyExc_TypeError, "'%s' is not matching expected type of '%S'", Py_TYPE(value)->tp_name, expected);
    return NULL;
}

/* a ValueError raised from the error set, which it replaces */
static PyObject *
value_error_from(const char *message)
{
    PyObject *cause = PyErr_GetRaisedException();
    PyErr_SetString(PyExc_ValueError, message);
    if (cause != NULL) {
        PyObject *exc = PyErr_GetRaisedException();
        PyException_SetCause(exc, cause);  /* steals the cause */
        PyErr_SetRaisedException(exc);
    }
    return NULL;
}

/* the error set, reported from the position named - which always leaves an error set */
static PyObject *
report_at(ModuleState *state, PyObject *segment)
{
    if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;  /* not ours to wrap */
    PyObject *exc = PyErr_GetRaisedException();
    PyObject *r = PyObject_CallFunctionObjArgs(state->report, segment, exc, NULL);
    if (r != NULL) {  /* report is documented to raise - make sure nothing is swallowed */
        Py_DECREF(r);
        PyErr_SetRaisedException(exc);
        return NULL;
    }
    Py_DECREF(exc);
    return NULL;
}

static PyObject *
report_at_index(ModuleState *state, Py_ssize_t index)
{
    PyObject *segment = PyUnicode_FromFormat("[%zd]", index);
    if (segment == NULL) return NULL;
    report_at(state, segment);
    Py_DECREF(segment);
    return NULL;
}

static PyObject *
report_at_key(ModuleState *state, PyObject *key, int quoted)
{
    PyObject *segment = PyUnicode_FromFormat(quoted ? "[\"%S\"]" : "[%S]", key);
    if (segment == NULL) return NULL;
    report_at(state, segment);
    Py_DECREF(segment);
    return NULL;
}

/* ---------------------------------------------------------- checks */

static int spec_check(ModuleState *state, Spec *spec, PyObject *value);
static PyObject *validate_spec(ModuleState *state, Spec *spec, PyObject *value);

static inline int
is_text_like(PyObject *value)
{
    return PyUnicode_Check(value) || PyBytes_Check(value) || PyByteArray_Check(value) || PyMemoryView_Check(value);
}

/* the target of an alias, compiled on its first use - NULL with an error set on failure */
static Spec *
alias_target(ModuleState *state, Spec *spec)
{
    Spec *target = spec->children[0];
    if (target != NULL) return target;
    PyObject *description = PyObject_CallOneArg(state->describe, spec->annotation);
    if (description == NULL) return NULL;
    Spec *compiled = spec_compile(description);
    Py_DECREF(description);
    if (compiled == NULL) return NULL;
    Py_BEGIN_CRITICAL_SECTION(spec->annotation);
    if (spec->children[0] == NULL) {
        spec->children[0] = compiled;
        spec->count = 1;
        compiled = NULL;
    }
    Py_END_CRITICAL_SECTION();
    spec_free(compiled);  /* another thread was first */
    return spec->children[0];
}

/* the elements of a value as a tuple, for what is accepted as a sequence: the tuple itself,
 * a snapshot of a list, NULL without an error for anything else - with an error for a
 * sequence which fails to iterate */
static PyObject *
sequence_snapshot(ModuleState *state, PyObject *value, int *owned)
{
    if (PyTuple_CheckExact(value)) {
        *owned = 0;
        return Py_NewRef(value);
    }
    *owned = 1;
    if (PyList_CheckExact(value)) return PyList_AsTuple(value);
    int r = PyObject_IsInstance(value, state->sequence);
    if (r <= 0) return NULL;
    return PySequence_Tuple(value);
}

/* the elements of a value as a tuple, for what is accepted as a set: a snapshot of the
 * set, or of any other iterable - NULL without an error for what is not iterable */
static PyObject *
iterable_snapshot(ModuleState *state, PyObject *value, int *owned)
{
    if (PyTuple_CheckExact(value)) {
        *owned = 0;
        return Py_NewRef(value);
    }
    *owned = 1;
    if (PyList_CheckExact(value) || PyAnySet_Check(value)) return PySequence_Tuple(value);
    int r = PyObject_IsInstance(value, state->iterable);
    if (r <= 0) return NULL;
    return PySequence_Tuple(value);
}

/* the items of a mapping as a list of pairs - NULL without an error for what is not one */
static PyObject *
mapping_snapshot(ModuleState *state, PyObject *value)
{
    if (!PyAnyDict_Check(value)) {
        int r = PyObject_IsInstance(value, state->mapping);
        if (r <= 0) return NULL;
    }
    return PyMapping_Items(value);
}

static int
check_elements(ModuleState *state, Spec *element, PyObject *items)
{
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(items); i++) {
        int r = spec_check(state, element, PyTuple_GET_ITEM(items, i));
        if (r <= 0) return r;
    }
    return 1;
}

/* whether a value is accepted as it is - what the first pass of a union decides by */
static int
spec_check_unbounded(ModuleState *state, Spec *spec, PyObject *value)
{
    switch (spec->kind) {
    case SPEC_ANY:
        return 1;
    case SPEC_NONE:
        return value == Py_None;
    case SPEC_MISSING:
        return value == state->missing;
    case SPEC_LITERAL:
        return PySequence_Contains(spec->data, value);
    case SPEC_BOOL:
        return PyBool_Check(value);
    case SPEC_INT:
        return PyLong_Check(value);
    case SPEC_FLOAT:
        return PyFloat_Check(value);
    case SPEC_STR:
        return PyUnicode_Check(value);
    case SPEC_BYTES:
        return PyBytes_Check(value);
    case SPEC_UUID:
        return PyObject_IsInstance(value, state->uuid);
    case SPEC_DATETIME:
        return PyObject_IsInstance(value, state->datetime);
    case SPEC_DATE:
        return PyObject_IsInstance(value, state->date);
    case SPEC_TIME:
        return PyObject_IsInstance(value, state->time);
    case SPEC_TIMEDELTA:
        return PyObject_IsInstance(value, state->timedelta);
    case SPEC_PATH:
        return PyObject_IsInstance(value, state->path);
    case SPEC_STR_ENUM:
    case SPEC_INT_ENUM:
    case SPEC_STATE:
    case SPEC_CUSTOM_STATE:
    case SPEC_CUSTOM:
        return PyObject_IsInstance(value, spec->base);
    case SPEC_META:
        return PyObject_IsInstance(value, state->meta);
    case SPEC_FUNCTION:
        return PyCallable_Check(value);
    case SPEC_TUPLE: {
        if (is_text_like(value)) return 0;
        int owned;
        PyObject *items = sequence_snapshot(state, value, &owned);
        if (items == NULL) return PyErr_Occurred() ? -1 : 0;
        int r = PyTuple_GET_SIZE(items) == spec->count;
        for (Py_ssize_t i = 0; r > 0 && i < spec->count; i++) r = spec_check(state, spec->children[i], PyTuple_GET_ITEM(items, i));
        Py_DECREF(items);
        return r;
    }
    case SPEC_SEQUENCE: {
        if (is_text_like(value)) return 0;
        int owned;
        PyObject *items = sequence_snapshot(state, value, &owned);
        if (items == NULL) return PyErr_Occurred() ? -1 : 0;
        int r = check_elements(state, spec->children[0], items);
        Py_DECREF(items);
        return r;
    }
    case SPEC_SET: {
        int r = PyAnySet_Check(value) ? 1 : PyObject_IsInstance(value, state->set);
        if (r <= 0) return r;
        PyObject *items = PySequence_Tuple(value);
        if (items == NULL) return -1;
        r = check_elements(state, spec->children[0], items);
        Py_DECREF(items);
        return r;
    }
    case SPEC_MAPPING: {
        PyObject *items = mapping_snapshot(state, value);
        if (items == NULL) return PyErr_Occurred() ? -1 : 0;
        int r = 1;
        for (Py_ssize_t i = 0; r > 0 && i < PyList_GET_SIZE(items); i++) {
            PyObject *pair = PyList_GET_ITEM(items, i);
            r = spec_check(state, spec->children[0], PyTuple_GET_ITEM(pair, 0));
            if (r > 0) r = spec_check(state, spec->children[1], PyTuple_GET_ITEM(pair, 1));
        }
        Py_DECREF(items);
        return r;
    }
    case SPEC_TYPED_DICT: {
        if (!PyAnyDict_Check(value)) {
            int r = PyObject_IsInstance(value, state->mapping);
            if (r <= 0) return r;
        }
        for (Py_ssize_t i = 0; i < spec->count; i++) {
            PyObject *entry = PyTuple_GET_ITEM(spec->data, i);
            PyObject *key = PyTuple_GET_ITEM(entry, 0);
            int required = PyTuple_GET_ITEM(entry, 1) == Py_True;
            PyObject *item = NULL;
            int present = PyMapping_GetOptionalItem(value, key, &item);
            if (present < 0) return -1;
            int ok = 0;
            if (present) {
                ok = spec_check(state, spec->children[i], item);
                Py_DECREF(item);
                if (ok < 0) return -1;
            }
            if (!ok && required) return 0;
        }
        return 1;
    }
    case SPEC_UNION:
        for (Py_ssize_t i = 0; i < spec->count; i++) {
            int r = spec_check(state, spec->children[i], value);
            if (r != 0) return r;
        }
        return 0;
    case SPEC_VALIDABLE:
        return spec_check(state, spec->children[0], value);
    case SPEC_ALIAS: {
        Spec *target = alias_target(state, spec);
        return target == NULL ? -1 : spec_check(state, target, value);
    }
    default:
        PyErr_Format(PyExc_TypeError, "unknown spec kind %d", spec->kind);
        return -1;
    }
}

static int
spec_check(ModuleState *state, Spec *spec, PyObject *value)
{
    if (!spec->nested) return spec_check_unbounded(state, spec, value);
    if (Py_EnterRecursiveCall(" while checking a value")) return -1;
    int r = spec_check_unbounded(state, spec, value);
    Py_LeaveRecursiveCall();
    return r;
}

/* -------------------------------------------------------- validation */

/*
 * The elements of a tuple validated with a spec each - the one of the sequence, or the
 * one of the position when `positional`. The result is a new reference: the tuple itself
 * when no element changes, a new tuple otherwise - or, when the tuple is `owned` (made
 * here, seen by nothing else), the tuple changed in place.
 */
static PyObject *
validate_elements(ModuleState *state, Spec *spec, int positional, PyObject *tuple, int owned)
{
    Py_ssize_t n = PyTuple_GET_SIZE(tuple);
    PyObject *result = NULL;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *element = PyTuple_GET_ITEM(tuple, i);
        Spec *element_spec = positional ? spec->children[i] : spec->children[0];
        PyObject *checked = validate_spec(state, element_spec, element);
        if (checked == NULL) {
            if (result != NULL && result != tuple) Py_DECREF(result);
            return report_at_index(state, i);
        }
        if (checked == element) {
            if (result != NULL && result != tuple) PyTuple_SET_ITEM(result, i, checked);
            else Py_DECREF(checked);
            continue;
        }
        if (result == NULL) {
            if (owned) {
                result = tuple;
            } else {
                result = PyTuple_New(n);
                if (result == NULL) {
                    Py_DECREF(checked);
                    return NULL;
                }
                for (Py_ssize_t j = 0; j < i; j++) PyTuple_SET_ITEM(result, j, Py_NewRef(PyTuple_GET_ITEM(tuple, j)));
            }
        }
        if (result == tuple) {
            PyObject *previous = PyTuple_GET_ITEM(tuple, i);
            PyTuple_SET_ITEM(tuple, i, checked);
            Py_DECREF(previous);
        } else {
            PyTuple_SET_ITEM(result, i, checked);
        }
    }
    return result == NULL || result == tuple ? Py_NewRef(tuple) : result;
}

/* a conversion of a str by a callable of the standard library, with the error it has */
static PyObject *
converted_str(PyObject *converter, PyObject *value, const char *failure)
{
    PyObject *converted = PyObject_CallOneArg(converter, value);
    if (converted != NULL) return converted;
    if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
    return value_error_from(failure);
}

static PyObject *
validate_enum_str(ModuleState *state, Spec *spec, PyObject *value)
{
    PyObject *converted = PyObject_CallOneArg(spec->base, value);
    if (converted != NULL) return converted;
    if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
    PyErr_Clear();
    converted = PyObject_GetItem(spec->base, value);
    if (converted != NULL) return converted;
    if (!PyErr_ExceptionMatches(PyExc_KeyError)) return NULL;
    PyObject *cause = PyErr_GetRaisedException();
    PyObject *allowed = PyObject_CallFunction(state->enum_values, "Oi", spec->base, 0);
    PyObject *name = allowed == NULL ? NULL : PyObject_GetAttrString(spec->base, "__name__");
    if (name != NULL) {
        PyErr_Format(PyExc_ValueError, "'%s' is not matching any of expected %U values [%U]", Py_TYPE(value)->tp_name, name, allowed);
        PyObject *exc = PyErr_GetRaisedException();
        PyException_SetCause(exc, cause);
        PyErr_SetRaisedException(exc);
    } else {
        Py_DECREF(cause);
    }
    Py_XDECREF(allowed);
    Py_XDECREF(name);
    return NULL;
}

static PyObject *
validate_enum_int(ModuleState *state, Spec *spec, PyObject *value)
{
    if (PyLong_Check(value)) {
        PyObject *converted = PyObject_CallOneArg(spec->base, value);
        if (converted != NULL) return converted;
        if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
        PyObject *cause = PyErr_GetRaisedException();
        PyObject *allowed = PyObject_CallFunction(state->enum_values, "Oi", spec->base, 0);
        PyObject *name = allowed == NULL ? NULL : PyObject_GetAttrString(spec->base, "__name__");
        if (name != NULL) {
            PyErr_Format(PyExc_ValueError, "'%s' is not matching any of expected %U values [%U]", Py_TYPE(value)->tp_name, name, allowed);
            PyObject *exc = PyErr_GetRaisedException();
            PyException_SetCause(exc, cause);
            PyErr_SetRaisedException(exc);
        } else {
            Py_DECREF(cause);
        }
        Py_XDECREF(allowed);
        Py_XDECREF(name);
        return NULL;
    }
    /* a str - the name of a member, or the number of one */
    PyObject *converted = PyObject_GetItem(spec->base, value);
    if (converted != NULL) return converted;
    if (!PyErr_ExceptionMatches(PyExc_KeyError)) return NULL;
    PyObject *cause = PyErr_GetRaisedException();
    PyObject *number = PyLong_FromUnicodeObject(value, 10);
    if (number != NULL) {
        converted = PyObject_CallOneArg(spec->base, number);
        Py_DECREF(number);
        if (converted != NULL) {
            Py_DECREF(cause);
            return converted;
        }
    }
    if (!PyErr_ExceptionMatches(PyExc_Exception)) {
        Py_DECREF(cause);
        return NULL;
    }
    PyErr_Clear();
    PyObject *allowed = PyObject_CallFunction(state->enum_values, "Oi", spec->base, 1);
    PyObject *name = allowed == NULL ? NULL : PyObject_GetAttrString(spec->base, "__name__");
    if (name != NULL) {
        PyErr_Format(PyExc_ValueError, "'%s' is not matching any of expected %U members [%U]", Py_TYPE(value)->tp_name, name, allowed);
        PyObject *exc = PyErr_GetRaisedException();
        PyException_SetCause(exc, cause);
        PyErr_SetRaisedException(exc);
    } else {
        Py_DECREF(cause);
    }
    Py_XDECREF(allowed);
    Py_XDECREF(name);
    return NULL;
}

static PyObject *struct_validate_value(ModuleState *state, PyObject *cls, PyObject *value);

/* the entries of a dictionary before a key, copied into another */
static int
copy_entries_before(PyObject *source, PyObject *key, PyObject *target)
{
    PyObject *k;
    PyObject *v;
    Py_ssize_t position = 0;
    while (PyDict_Next(source, &position, &k, &v)) {
        if (k == key) return 0;
        if (PyDict_SetItem(target, k, v) < 0) return -1;
    }
    return 0;
}

/*
 * A dictionary is validated through a private copy, which is the result when no key
 * converts - nothing here reads what another thread could be changing, and a value of
 * an existing key is set while iterating, which keeps the keys. A frozendict can't
 * change, so it is read as it is, and a Map holding nothing which converts is the result
 * already. Any other mapping is copied into a dictionary first.
 */
static PyObject *
validate_mapping(ModuleState *state, Spec *spec, PyObject *value)
{
    PyObject *source;  /* what is iterated */
    int owned;         /* whether the source is private, and so the result to be */
    if (PyDict_Check(value)) {
        source = PyDict_Copy(value);
        owned = 1;
    } else if (PyFrozenDict_Check(value)) {
        source = Py_NewRef(value);
        owned = 0;
    } else {
        int r = PyObject_IsInstance(value, state->mapping);
        if (r < 0) return NULL;
        if (!r) return type_error("Mapping", value);
        source = PyDict_New();
        if (source != NULL && PyDict_Update(source, value) < 0) Py_CLEAR(source);
        owned = 1;
    }
    if (source == NULL) return NULL;

    PyObject *result = owned ? Py_NewRef(source) : NULL;  /* made when a value converts */
    PyObject *rekeyed = NULL;  /* made when a key converts, which changes the layout */
    PyObject *key;
    PyObject *item;
    Py_ssize_t position = 0;
    while (PyDict_Next(source, &position, &key, &item)) {
        PyObject *checked_key = validate_spec(state, spec->children[0], key);
        PyObject *checked_item = checked_key == NULL ? NULL : validate_spec(state, spec->children[1], item);
        if (checked_item == NULL) {
            Py_XDECREF(checked_key);
            report_at_key(state, key, 0);
            goto fail;
        }
        int failed = 0;
        if (checked_key != key || rekeyed != NULL) {
            if (rekeyed == NULL) {
                rekeyed = PyDict_New();
                failed = rekeyed == NULL || copy_entries_before(result != NULL ? result : source, key, rekeyed) < 0;
            }
            if (!failed) failed = PyDict_SetItem(rekeyed, checked_key, checked_item) < 0;
        } else if (checked_item != item) {
            if (result == NULL) {
                result = PyDict_New();
                failed = result == NULL || copy_entries_before(source, key, result) < 0;
            }
            if (!failed) failed = PyDict_SetItem(result, key, checked_item) < 0;
        } else if (result != NULL && result != source) {
            failed = PyDict_SetItem(result, key, item) < 0;
        }
        Py_DECREF(checked_key);
        Py_DECREF(checked_item);
        if (failed) goto fail;
    }

    PyObject *validated = rekeyed != NULL ? rekeyed : result;
    if (rekeyed != NULL) Py_XDECREF(result);
    Py_DECREF(source);
    if (validated == NULL) {
        /* a frozendict holding nothing which converts - the Map it is, or the Map of it */
        if (PyObject_TypeCheck(value, (PyTypeObject *)state->map)) return Py_NewRef(value);
        return PyObject_CallOneArg(state->map, value);
    }
    PyObject *mapped = PyObject_CallOneArg(state->map, validated);
    Py_DECREF(validated);
    return mapped;

fail:
    Py_XDECREF(rekeyed);
    Py_XDECREF(result);
    Py_DECREF(source);
    return NULL;
}

static PyObject *
validate_typed_dict(ModuleState *state, Spec *spec, PyObject *value)
{
    if (!PyAnyDict_Check(value)) {
        int r = PyObject_IsInstance(value, state->mapping);
        if (r < 0) return NULL;
        if (!r) return type_error_of(spec->base, value);
    }
    PyObject *result = PyDict_New();
    if (result == NULL) return NULL;
    for (Py_ssize_t i = 0; i < spec->count; i++) {
        PyObject *entry = PyTuple_GET_ITEM(spec->data, i);
        PyObject *key = PyTuple_GET_ITEM(entry, 0);
        PyObject *item = NULL;
        int present = PyMapping_GetOptionalItem(value, key, &item);
        if (present < 0) goto fail;
        if (!present) {
            if (PyTuple_GET_ITEM(entry, 1) == Py_True) {
                PyErr_Format(PyExc_KeyError, "Value for '%U' is required", key);
                report_at_key(state, key, 1);
                goto fail;
            }
            continue;  /* an optional key which was not provided */
        }
        PyObject *checked = validate_spec(state, spec->children[i], item);
        Py_DECREF(item);
        if (checked == NULL) {
            report_at_key(state, key, 1);
            goto fail;
        }
        int failed = PyDict_SetItem(result, key, checked) < 0;
        Py_DECREF(checked);
        if (failed) goto fail;
    }
    PyObject *mapped = PyObject_CallOneArg(state->map, result);
    Py_DECREF(result);
    return mapped;
fail:
    Py_DECREF(result);
    return NULL;
}

static PyObject *
validate_union(ModuleState *state, Spec *spec, PyObject *value)
{
    PyObject *errors = PyList_New(0);
    if (errors == NULL) return NULL;
    /* an alternative the value already matches wins over one which would only convert
     * it - resolving in the declaration order alone would let the `float` of
     * `str | float | int` turn every int into a float */
    for (int pass = 0; pass < 2; pass++) {
        for (Py_ssize_t i = 0; i < spec->count; i++) {
            Spec *alternative = spec->children[i];
            if (pass == 0) {
                int r = spec_check(state, alternative, value);
                if (r < 0) {
                    if (!PyErr_ExceptionMatches(PyExc_Exception)) goto fail;
                    PyObject *exc = PyErr_GetRaisedException();
                    int failed = PyList_Append(errors, exc) < 0;
                    Py_DECREF(exc);
                    if (failed) goto fail;
                    continue;
                }
                if (!r) continue;
            }
            PyObject *validated = validate_spec(state, alternative, value);
            if (validated != NULL) {
                Py_DECREF(errors);
                return validated;
            }
            if (!PyErr_ExceptionMatches(PyExc_Exception)) goto fail;
            PyObject *exc = PyErr_GetRaisedException();
            int failed = PyList_Append(errors, exc) < 0;
            Py_DECREF(exc);
            if (failed) goto fail;
        }
    }
    PyObject *names = PyList_New(0);
    if (names == NULL) goto fail;
    for (Py_ssize_t i = 0; i < spec->count; i++) {
        PyObject *name = PyObject_GetAttr(spec->children[i]->annotation, state->str_type_name);
        int failed = name == NULL || PyList_Append(names, name) < 0;
        Py_XDECREF(name);
        if (failed) {
            Py_DECREF(names);
            goto fail;
        }
    }
    PyObject *separator = PyUnicode_FromString(" | ");
    PyObject *joined = separator == NULL ? NULL : PyUnicode_Join(separator, names);
    Py_XDECREF(separator);
    Py_DECREF(names);
    if (joined == NULL) goto fail;
    PyObject *message = PyUnicode_FromFormat(
        "'%s' is not matching any of the allowed alternatives: %U", Py_TYPE(value)->tp_name, joined);
    Py_DECREF(joined);
    if (message == NULL) goto fail;
    PyObject *group = PyObject_CallFunctionObjArgs(state->exception_group, message, errors, NULL);
    Py_DECREF(message);
    if (group != NULL) {
        PyErr_SetRaisedException(group);
    }
fail:
    Py_DECREF(errors);
    return NULL;
}

static PyObject *
validate_kind(ModuleState *state, Spec *spec, PyObject *value)
{
    switch (spec->kind) {
    case SPEC_ANY:
        return Py_NewRef(value);
    case SPEC_NONE:
        return value == Py_None ? Py_NewRef(value) : type_error("None", value);
    case SPEC_MISSING:
        return value == state->missing ? Py_NewRef(value) : type_error("Missing", value);
    case SPEC_LITERAL: {
        int r = PySequence_Contains(spec->data, value);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        PyObject *spelled = PyObject_CallOneArg(state->literal_values, spec->data);
        if (spelled == NULL) return NULL;
        PyErr_Format(PyExc_ValueError, "'%s' is not matching any of expected literal values [%U]", Py_TYPE(value)->tp_name, spelled);
        Py_DECREF(spelled);
        return NULL;
    }
    case SPEC_BOOL:
        if (PyBool_Check(value)) return Py_NewRef(value);
        if (PyLong_Check(value)) {
            int r = PyObject_IsTrue(value);
            if (r < 0) return NULL;
            return PyBool_FromLong(r);
        }
        if (PyUnicode_Check(value)) {
            PyObject *lowered = PyObject_CallMethodNoArgs(value, state->str_lower);
            if (lowered == NULL) return NULL;
            int is_true = PyUnicode_CompareWithASCIIString(lowered, "true") == 0;
            int is_false = !is_true && PyUnicode_CompareWithASCIIString(lowered, "false") == 0;
            Py_DECREF(lowered);
            if (is_true) return Py_NewRef(Py_True);
            if (is_false) return Py_NewRef(Py_False);
            PyErr_SetString(PyExc_ValueError, "'str' value is not matching any of expected values [True, False]");
            return NULL;
        }
        return type_error("bool", value);
    case SPEC_INT:
        if (PyBool_Check(value)) return type_error("int", value);
        if (PyLong_Check(value)) return Py_NewRef(value);
        if (PyFloat_Check(value)) {
            double number = PyFloat_AsDouble(value);
            if (number == -1.0 && PyErr_Occurred()) return NULL;
            if (isfinite(number) && floor(number) == number) return PyLong_FromDouble(number);
            return type_error("int", value);
        }
        if (PyUnicode_Check(value)) {
            /* json spells every mapping key as a str, so an `int` key encodes to one - it
             * has to be readable back the same way */
            PyObject *converted = PyLong_FromUnicodeObject(value, 10);
            if (converted != NULL) return converted;
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
            return value_error_from("'str' value is not matching expected format of 'int'");
        }
        return type_error("int", value);
    case SPEC_FLOAT:
        if (PyBool_Check(value)) return type_error("float", value);
        if (PyFloat_Check(value)) return Py_NewRef(value);
        if (PyLong_Check(value)) return PyNumber_Float(value);
        if (PyUnicode_Check(value)) {
            PyObject *converted = PyFloat_FromString(value);
            if (converted != NULL) return converted;
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
            return value_error_from("'str' value is not matching expected format of 'float'");
        }
        return type_error("float", value);
    case SPEC_STR:
        return PyUnicode_Check(value) ? Py_NewRef(value) : type_error("str", value);
    case SPEC_BYTES:
        if (PyBytes_Check(value)) return Py_NewRef(value);
        if (PyUnicode_Check(value)) {
            /* base64 - the spelling `basic_value` encodes `bytes` to, and the only one json
             * can carry. The field declares `bytes`, so a str here is unambiguous */
            return converted_str(state->b64decode, value, "'str' value is not matching expected base64 format for 'bytes'");
        }
        return type_error("bytes", value);
    case SPEC_UUID: {
        int r = PyObject_IsInstance(value, state->uuid);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        if (PyUnicode_Check(value)) return converted_str(state->uuid, value, "'str' value is not matching expected format of 'UUID'");
        return type_error("UUID", value);
    }
    case SPEC_DATETIME: {
        int r = PyObject_IsInstance(value, state->datetime);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        if (PyUnicode_Check(value)) {
            PyObject *converter = PyObject_GetAttr(state->datetime, state->str_fromisoformat);
            if (converter == NULL) return NULL;
            PyObject *converted = converted_str(converter, value, "'str' value is not matching expected ISO format for 'datetime'");
            Py_DECREF(converter);
            return converted;
        }
        return type_error("datetime", value);
    }
    case SPEC_DATE: {
        int r = PyObject_IsInstance(value, state->date);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        if (PyUnicode_Check(value)) {
            PyObject *converter = PyObject_GetAttr(state->date, state->str_fromisoformat);
            if (converter == NULL) return NULL;
            PyObject *converted = converted_str(converter, value, "'str' value is not matching expected ISO format for 'date'");
            Py_DECREF(converter);
            return converted;
        }
        return type_error("date", value);
    }
    case SPEC_TIME: {
        int r = PyObject_IsInstance(value, state->time);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        if (PyUnicode_Check(value)) {
            PyObject *converter = PyObject_GetAttr(state->time, state->str_fromisoformat);
            if (converter == NULL) return NULL;
            PyObject *converted = converted_str(converter, value, "'str' value is not matching expected ISO format for 'time'");
            Py_DECREF(converter);
            return converted;
        }
        return type_error("time", value);
    }
    case SPEC_TIMEDELTA: {
        int r = PyObject_IsInstance(value, state->timedelta);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        if (PyUnicode_Check(value)) {
            /* ISO 8601 duration - the spelling `basic_value` encodes `timedelta` to */
            return converted_str(state->parse_duration, value, "'str' value is not matching expected ISO 8601 duration format for 'timedelta'");
        }
        return type_error("timedelta", value);
    }
    case SPEC_PATH: {
        int r = PyObject_IsInstance(value, state->path);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        r = PyUnicode_Check(value) ? 1 : PyObject_IsInstance(value, state->pathlike);
        if (r < 0) return NULL;
        if (r) {
            PyObject *converted = PyObject_CallOneArg(state->path, value);
            if (converted != NULL) return converted;
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
            PyObject *cause = PyErr_GetRaisedException();
            PyErr_Format(PyExc_ValueError, "'%s' value is not matching expected path format", Py_TYPE(value)->tp_name);
            PyObject *exc = PyErr_GetRaisedException();
            PyException_SetCause(exc, cause);
            PyErr_SetRaisedException(exc);
            return NULL;
        }
        return type_error("Path", value);
    }
    case SPEC_STR_ENUM: {
        int r = PyObject_IsInstance(value, spec->base);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        if (PyUnicode_Check(value)) return validate_enum_str(state, spec, value);
        return type_error_of(spec->base, value);
    }
    case SPEC_INT_ENUM: {
        int r = PyObject_IsInstance(value, spec->base);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        if (PyLong_Check(value) || PyUnicode_Check(value)) return validate_enum_int(state, spec, value);
        return type_error_of(spec->base, value);
    }
    case SPEC_TUPLE: {
        if (is_text_like(value)) return type_error("tuple", value);
        int owned;
        PyObject *items = sequence_snapshot(state, value, &owned);
        if (items == NULL) return PyErr_Occurred() ? NULL : type_error("tuple", value);
        if (PyTuple_GET_SIZE(items) != spec->count) {
            PyErr_Format(PyExc_ValueError, "'%s' of length %zd does not match expected tuple length %zd",
                         Py_TYPE(value)->tp_name, PyTuple_GET_SIZE(items), spec->count);
            Py_DECREF(items);
            return NULL;
        }
        PyObject *result = validate_elements(state, spec, 1, items, owned);
        Py_DECREF(items);
        return result;
    }
    case SPEC_SEQUENCE: {
        if (is_text_like(value)) return type_error("Sequence", value);
        int owned;
        PyObject *items = sequence_snapshot(state, value, &owned);
        if (items == NULL) return PyErr_Occurred() ? NULL : type_error("Sequence", value);
        PyObject *result = validate_elements(state, spec, 0, items, owned);
        Py_DECREF(items);
        return result;
    }
    case SPEC_SET: {
        if (is_text_like(value)) return type_error("Set", value);
        int owned;
        PyObject *items = iterable_snapshot(state, value, &owned);
        if (items == NULL) return PyErr_Occurred() ? NULL : type_error("Set", value);
        PyObject *checked = validate_elements(state, spec, 0, items, owned);
        int unchanged = checked == items;
        Py_DECREF(items);
        if (checked == NULL) return NULL;
        if (unchanged && PyFrozenSet_CheckExact(value)) {
            Py_DECREF(checked);
            return Py_NewRef(value);
        }
        PyObject *frozen = PyFrozenSet_New(checked);
        Py_DECREF(checked);
        return frozen;
    }
    case SPEC_MAPPING:
        return validate_mapping(state, spec, value);
    case SPEC_META: {
        int r = PyObject_IsInstance(value, state->meta);
        if (r < 0) return NULL;
        if (r) return Py_NewRef(value);
        r = PyAnyDict_Check(value) ? 1 : PyObject_IsInstance(value, state->mapping);
        if (r < 0) return NULL;
        if (r) return PyObject_CallMethodOneArg(state->meta, state->str_validate, value);
        return type_error("Meta", value);
    }
    case SPEC_TYPED_DICT:
        return validate_typed_dict(state, spec, value);
    case SPEC_STATE:
        return struct_validate_value(state, spec->base, value);
    case SPEC_CUSTOM_STATE:
        return PyObject_CallMethodOneArg(spec->base, state->str_validate, value);
    case SPEC_CUSTOM: {
        /* a class providing its own `validate` converts the input as the innermost step */
        PyObject *validated;
        PyObject *validate;
        if (PyObject_GetOptionalAttr(spec->base, state->str_validate, &validate) < 0) return NULL;
        if (validate != NULL) {
            validated = PyObject_CallOneArg(validate, value);
            Py_DECREF(validate);
            if (validated == NULL) return NULL;
        } else {
            validated = Py_NewRef(value);
        }
        int r = PyObject_IsInstance(validated, spec->base);
        if (r > 0) return validated;
        if (r == 0) type_error_of(spec->base, validated);
        Py_DECREF(validated);
        return NULL;
    }
    case SPEC_FUNCTION:
        if (PyCallable_Check(value)) return Py_NewRef(value);
        PyErr_Format(PyExc_TypeError, "'%s' is not matching expected function type", Py_TYPE(value)->tp_name);
        return NULL;
    case SPEC_UNION:
        return validate_union(state, spec, value);
    case SPEC_VALIDABLE:
        return validate_spec(state, spec->children[0], value);  /* `validating` ran already */
    case SPEC_ALIAS: {
        Spec *target = alias_target(state, spec);
        return target == NULL ? NULL : validate_spec(state, target, value);
    }
    default:
        PyErr_Format(PyExc_TypeError, "unknown spec kind %d", spec->kind);
        return NULL;
    }
}

static PyObject *
validate_spec_unbounded(ModuleState *state, Spec *spec, PyObject *value)
{
    PyObject *raw = value;
    if (spec->validating != NULL) {
        raw = PyObject_CallOneArg(spec->validating, value);
        if (raw == NULL) return NULL;
    }
    PyObject *result = validate_kind(state, spec, raw);
    if (raw != value) Py_DECREF(raw);
    if (result != NULL && spec->verifying != NULL) {
        PyObject *verified = PyObject_CallOneArg(spec->verifying, result);
        Py_DECREF(result);
        result = verified;
    }
    return result;
}

/* the value a spec accepts for the one given - a new reference, NULL with the error set */
static PyObject *
validate_spec(ModuleState *state, Spec *spec, PyObject *value)
{
    if (!spec->nested) return validate_spec_unbounded(state, spec, value);  /* a leaf recurses into nothing */
    if (Py_EnterRecursiveCall(" while validating a value")) return NULL;
    PyObject *result = validate_spec_unbounded(state, spec, value);
    Py_LeaveRecursiveCall();
    return result;
}

/* validate(spec, value) - the value as the spec accepts it */
static PyObject *
validate_native(PyObject *mod, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "validate expects a spec and a value");
        return NULL;
    }
    ModuleState *state = module_state(mod);
    Spec *spec = spec_of(state, args[0]);
    if (spec == NULL) return NULL;
    return validate_spec(state, spec, args[1]);
}

/* check(spec, value) - whether the value is accepted as it is */
static PyObject *
check_native(PyObject *mod, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "check expects a spec and a value");
        return NULL;
    }
    ModuleState *state = module_state(mod);
    Spec *spec = spec_of(state, args[0]);
    if (spec == NULL) return NULL;
    int r = spec_check(state, spec, args[1]);
    if (r < 0) return NULL;
    return PyBool_FromLong(r);
}

