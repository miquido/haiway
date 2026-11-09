/* ----------------------------------------------------------------- plan */

/*
 * The plan of a class, as C data. It is the only description of the attributes of a
 * class the native code has: what they are called, what validates them, what they
 * default to, and where each is read from - the member descriptor of an attribute reads
 * the definition kept here. The names are compared by identity (they are interned, as
 * are the keyword names of a call), and no dictionary of the keyword arguments is built
 * at all for the usual call. Whatever does not fit - a name which is not interned, one
 * which is not an attribute, both an attribute and its alias, a lot of arguments - takes
 * the general path, which is the one telling what is wrong with it.
 */
#define FAST_MAX_FIELDS 64

typedef struct {
    PyObject *name;
    PyObject *alias;  /* NULL when there is none */
    const char *name_utf8;   /* the spellings json keys are compared with, in place */
    Py_ssize_t name_length;
    const char *alias_utf8;  /* NULL when there is no alias */
    Py_ssize_t alias_length;
    const char *json_key;    /* the key as json spells it, with the colon - `"key": ` */
    Py_ssize_t json_key_length;
    Spec *spec;       /* owned by the spec object the plan tuple holds */
    PyObject *default_value;  /* () -> the validated default */
    PyObject *cached;  /* the validated default, when it is a constant nothing can change - NULL otherwise */
    PyMemberDef member;  /* what the descriptor of the attribute reads */
} FieldPlan;

/* a key of a json object hashed by its bytes, to find the attribute it names without a str */
typedef struct {
    const char *text;  /* NULL for an empty slot */
    Py_ssize_t length;
    Py_ssize_t index;
} KeySlot;

typedef struct CompiledPlan {
    ModuleState *state;
    PyObject *owner;  /* the plan tuple - what the pointers above point into, cleared with the class */
    PyObject *names;  /* the names and aliases - what the spellings of the fields point into */
    PyObject *json_keys;  /* the json spellings of the keys, as bytes */
    PyObject *keys;   /* every name and alias, with the position of its attribute */
    PyObject *cached; /* the cached defaults, as a list */
    KeySlot *key_slots;  /* every name and alias by the hash of its bytes, open addressing */
    size_t key_mask;
    int reporting;    /* whether a failure is reported at its position, and a key which is not an attribute refused */
    Py_ssize_t count;
    FieldPlan fields[];
} CompiledPlan;

static void
plan_free(CompiledPlan *plan)
{
    if (plan == NULL) return;
    Py_XDECREF(plan->owner);
    Py_XDECREF(plan->names);
    Py_XDECREF(plan->json_keys);
    Py_XDECREF(plan->keys);
    Py_XDECREF(plan->cached);
    PyMem_RawFree(plan->key_slots);
    PyMem_RawFree(plan);
}

static inline size_t
key_hash(const char *text, Py_ssize_t length)
{
    size_t hash = 14695981039346656037ULL;  /* FNV-1a */
    for (Py_ssize_t i = 0; i < length; i++) hash = (hash ^ (unsigned char)text[i]) * 1099511628211ULL;
    return hash;
}

static int
key_slots_add(CompiledPlan *plan, const char *text, Py_ssize_t length, Py_ssize_t index)
{
    size_t at = key_hash(text, length) & plan->key_mask;
    while (plan->key_slots[at].text != NULL) at = (at + 1) & plan->key_mask;
    plan->key_slots[at].text = text;
    plan->key_slots[at].length = length;
    plan->key_slots[at].index = index;
    return 0;
}

/* the attribute a key spelled by these bytes names, -1 for none */
static inline Py_ssize_t
field_for_bytes(CompiledPlan *plan, const char *text, Py_ssize_t length)
{
    size_t at = key_hash(text, length) & plan->key_mask;
    for (;;) {
        KeySlot *slot = &plan->key_slots[at];
        if (slot->text == NULL) return -1;
        if (slot->length == length && memcmp(slot->text, text, (size_t)length) == 0) return slot->index;
        at = (at + 1) & plan->key_mask;
    }
}

/* the key of an attribute as json spells it, followed by the colon - defined with the json writer */
static PyObject *json_key_bytes(PyObject *key);

/* the compiled plan of a class, or the error of there being none */
static inline CompiledPlan *
plan_or_error(PyTypeObject *type)
{
    CompiledPlan *plan = PLAN_OF(type);
    if (plan == NULL) PyErr_Format(PyExc_TypeError, "%s was never given a plan", type->tp_name);
    return plan;
}

static inline PyObject *
validate_field(FieldPlan *field, CompiledPlan *plan, PyObject *value)
{
    return validate_spec(plan->state, field->spec, value);
}

/* the default of an attribute which was not given, validated */
static inline PyObject *
field_default(FieldPlan *field)
{
    if (field->cached != NULL) return Py_NewRef(field->cached);
    return PyObject_CallNoArgs(field->default_value);
}

/*
 * A failed validation of an attribute. Unexpected and duplicated keys take precedence over
 * it, the way they do when they are checked before any value is validated - so they are
 * checked here, and only here, since a failure is the exception. Leaves an error set.
 */
static void
report_failure(ModuleState *state, PyTypeObject *type, PyObject *kwargs, PyObject *name)
{
    if (!PyErr_ExceptionMatches(PyExc_Exception)) return;  /* not ours to wrap */
    if (!PLAN_OF(type)->reporting) return;  /* a plain record raises what fails as it is */
    if (kwargs != NULL) {
        PyObject *exc = PyErr_GetRaisedException();
        PyObject *r = PyObject_CallFunctionObjArgs(state->check, (PyObject *)type, kwargs, NULL);
        if (r == NULL) {
            Py_DECREF(exc);
            return;
        }
        Py_DECREF(r);
        PyErr_SetRaisedException(exc);
    }
    PyObject *segment = PyUnicode_FromFormat(".%U", name);
    if (segment == NULL) return;
    report_at(state, segment);
    Py_DECREF(segment);
}

/* raises the error of unexpected or duplicated keys, which are known to be there */
static int
raise_key_error(ModuleState *state, PyTypeObject *type, PyObject *kwargs)
{
    PyObject *r = PyObject_CallFunctionObjArgs(state->check, (PyObject *)type, kwargs, NULL);
    if (r != NULL) {
        Py_DECREF(r);
        PyErr_SetString(PyExc_TypeError, "unexpected attributes");
    }
    return -1;
}

/* the key an attribute has in a mapping or in json - its alias when it has one */
static inline PyObject *
field_key(FieldPlan *field)
{
    return field->alias != NULL ? field->alias : field->name;
}

/* the position of the attribute a key names, -1 for a key which names none */
static Py_ssize_t
field_for_key(CompiledPlan *plan, PyObject *key)
{
    PyObject *index;
    int r = PyDict_GetItemRef(plan->keys, key, &index);
    if (r <= 0) {
        if (r < 0) PyErr_Clear();  /* an unhashable key names nothing */
        return -1;
    }
    Py_ssize_t position = PyLong_AsSsize_t(index);
    Py_DECREF(index);
    return position;
}

/*
 * An instance is tracked by the garbage collector from its allocation. One holding
 * nothing the collector tracks can never be part of a cycle - and since nothing can
 * ever be bound to it later, it is let go of for good once it is built. A plain record
 * can be rebound, so it stays tracked.
 */
static inline void
finish_construction(PyObject *self)
{
    if (!PLAN_OF(Py_TYPE(self))->reporting) return;
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < n; i++) {
        if (items[i] != NULL && PyObject_GC_IsTracked(items[i])) return;
    }
    PyObject_GC_UnTrack(self);
}

static PyObject *struct_vectorcall(PyObject *callable, PyObject *const *args, size_t nargsf, PyObject *kwnames);
static PyObject *struct_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
static int struct_init(PyObject *obj, PyObject *args, PyObject *kwargs);

/* the entries of the plan handed over by Python, one for each attribute */
#define E_NAME(e) PyTuple_GET_ITEM((e), 0)
#define E_ALIAS(e) PyTuple_GET_ITEM((e), 1)
#define E_SPEC(e) PyTuple_GET_ITEM((e), 2)
#define E_DEFAULT(e) PyTuple_GET_ITEM((e), 3)
#define E_CONSTANT(e) PyTuple_GET_ITEM((e), 4)

/*
 * compile_plan(cls, plan, owned, reporting) - the plan of the class as C data, kept on
 * the class, and a read-only descriptor on the class for each of the attributes it
 * declares itself (`owned`, a tuple of their names - the others read theirs through the
 * descriptors of a base). `plan` is a tuple with an entry for each attribute:
 *
 *   (name, alias, spec, default, constant)
 *
 * `spec` is the compiled description of the annotation, `default` what is called for
 * the validated default of an attribute which was not given, and `constant` whether it
 * is the same every time - the validated default of such an attribute is kept here when
 * the spec promises an immutable result with no code of the user involved. A default
 * which fails to validate is left to fail where the instance is created.
 * `reporting` tells a class which validates: a failure is reported at its position and a
 * keyword which is not an attribute refused - a plain record raises what fails as it is
 * and passes over what it does not know.
 * The size of an instance is decided here, so the class can't have been instantiated
 * before, nor be given a plan twice. The call of the class goes through the plan when
 * the class does not take over its construction.
 */
static PyObject *
compile_plan(PyObject *mod, PyObject *args)
{
    ModuleState *state = module_state(mod);
    PyObject *cls;
    PyObject *entries;
    PyObject *owned;
    int reporting;
    if (!PyArg_ParseTuple(args, "OO!O!p", &cls, &PyTuple_Type, &entries, &PyTuple_Type, &owned, &reporting)) return NULL;
    if (!PyType_Check(cls) || !PyType_IsSubtype((PyTypeObject *)cls, state->struct_type)) {
        PyErr_SetString(PyExc_TypeError, "compile_plan expects a Struct class");
        return NULL;
    }
    PyTypeObject *type = (PyTypeObject *)cls;
    if (PLAN_OF(type) != NULL) {
        PyErr_Format(PyExc_TypeError, "%s was given a plan already", type->tp_name);
        return NULL;
    }
    Py_ssize_t n = PyTuple_GET_SIZE(entries);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *entry = PyTuple_GET_ITEM(entries, i);
        if (!PyTuple_Check(entry) || PyTuple_GET_SIZE(entry) != 5 || !PyUnicode_CheckExact(E_NAME(entry))
            || (E_ALIAS(entry) != Py_None && !PyUnicode_CheckExact(E_ALIAS(entry)))
            || !Py_IS_TYPE(E_SPEC(entry), state->spec_type) || !PyBool_Check(E_CONSTANT(entry))) {
            PyErr_SetString(PyExc_TypeError, "compile_plan expects entries of (name, alias, spec, default, constant)");
            return NULL;
        }
    }

    CompiledPlan *plan = PyMem_RawMalloc(sizeof(CompiledPlan) + (size_t)n * sizeof(FieldPlan));
    if (plan == NULL) return PyErr_NoMemory();
    plan->state = state;
    plan->owner = Py_NewRef(entries);
    plan->names = NULL;
    plan->json_keys = NULL;
    plan->keys = NULL;
    plan->cached = NULL;
    plan->key_slots = NULL;
    plan->reporting = reporting;
    plan->count = n;
    plan->names = PyTuple_New(2 * n);
    plan->json_keys = PyTuple_New(n);
    plan->keys = PyDict_New();
    plan->cached = PyList_New(0);
    /* room for every name and alias at most half full, so a search always ends */
    size_t slots = 8;
    while (slots < (size_t)n * 4) slots *= 2;
    plan->key_slots = PyMem_RawCalloc(slots, sizeof(KeySlot));
    plan->key_mask = slots - 1;
    if (plan->names == NULL || plan->json_keys == NULL || plan->keys == NULL || plan->cached == NULL
        || plan->key_slots == NULL) {
        if (plan->key_slots == NULL) PyErr_NoMemory();
        goto fail;
    }

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *entry = PyTuple_GET_ITEM(entries, i);
        FieldPlan *field = &plan->fields[i];
        field->name = E_NAME(entry);
        field->alias = E_ALIAS(entry) == Py_None ? NULL : E_ALIAS(entry);
        field->spec = ((SpecObject *)E_SPEC(entry))->spec;
        field->default_value = E_DEFAULT(entry);
        field->cached = NULL;
        PyTuple_SET_ITEM(plan->names, 2 * i, Py_NewRef(field->name));
        PyTuple_SET_ITEM(plan->names, 2 * i + 1, Py_NewRef(field->alias != NULL ? field->alias : Py_None));
        /* the spellings point into the strings, which the plan holds */
        const char *utf8 = PyUnicode_AsUTF8AndSize(field->name, &field->name_length);
        if (utf8 == NULL) goto fail;
        field->name_utf8 = utf8;
        field->alias_utf8 = NULL;
        field->alias_length = 0;
        if (field->alias != NULL) {
            field->alias_utf8 = PyUnicode_AsUTF8AndSize(field->alias, &field->alias_length);
            if (field->alias_utf8 == NULL) goto fail;
            key_slots_add(plan, field->alias_utf8, field->alias_length, i);
        }
        key_slots_add(plan, field->name_utf8, field->name_length, i);
        PyObject *json_key = json_key_bytes(field_key(field));
        if (json_key == NULL) goto fail;
        PyTuple_SET_ITEM(plan->json_keys, i, json_key);
        field->json_key = PyBytes_AS_STRING(json_key);
        field->json_key_length = PyBytes_GET_SIZE(json_key);
        field->member.name = utf8;
        field->member.type = Py_T_OBJECT_EX;
        field->member.offset = (Py_ssize_t)sizeof(PyObject) + i * (Py_ssize_t)sizeof(PyObject *);
        /* a value of a validated class is bound once, for good. a plain record refuses
         * `setattr` the same way, but keeps the way around it `object.__setattr__` has
         * always been for it - the descriptor itself writes */
        field->member.flags = reporting ? Py_READONLY : 0;
        field->member.doc = NULL;

        PyObject *position = PyLong_FromSsize_t(i);
        if (position == NULL) goto fail;
        int failed = PyDict_SetItem(plan->keys, field->name, position) < 0
            || (field->alias != NULL && PyDict_SetItem(plan->keys, field->alias, position) < 0);
        Py_DECREF(position);
        if (failed) goto fail;

        if (E_CONSTANT(entry) == Py_True && field->spec->pure) {
            PyObject *validated = PyObject_CallNoArgs(field->default_value);
            if (validated == NULL) {
                if (!PyErr_ExceptionMatches(PyExc_Exception)) goto fail;
                PyErr_Clear();  /* left to fail where the instance is created */
            } else {
                failed = PyList_Append(plan->cached, validated) < 0;
                Py_DECREF(validated);
                if (failed) goto fail;
                field->cached = validated;  /* the list holds it */
            }
        }
    }

    /* the descriptors of the attributes declared here - a base declares the rest */
    for (Py_ssize_t k = 0; k < PyTuple_GET_SIZE(owned); k++) {
        PyObject *name = PyTuple_GET_ITEM(owned, k);
        Py_ssize_t i = field_for_key(plan, name);
        if (i < 0 || plan->fields[i].name != name) {
            /* owned names are interned attribute names, never aliases */
            PyObject *interned = NULL;
            for (Py_ssize_t j = 0; j < n; j++) {
                int same = PyUnicode_Compare(plan->fields[j].name, name);
                if (same == 0) {
                    i = j;
                    interned = plan->fields[j].name;
                    break;
                }
                if (same < 0 && PyErr_Occurred()) goto fail;
            }
            if (interned == NULL) {
                PyErr_Format(PyExc_TypeError, "%U is not an attribute of %s", name, type->tp_name);
                goto fail;
            }
        }
        PyObject *descriptor = PyDescr_NewMember(type, &plan->fields[i].member);
        if (descriptor == NULL) goto fail;
        int failed = PyObject_SetAttr(cls, plan->fields[i].name, descriptor);
        Py_DECREF(descriptor);
        if (failed < 0) goto fail;
    }

    /* the size of an instance, and the call of the class - a class taking over its
     * construction, or one with more attributes than the fast path matches, is called
     * the usual way */
    type->tp_basicsize = (Py_ssize_t)sizeof(PyObject) + n * (Py_ssize_t)sizeof(PyObject *);
    PLAN_OF(type) = plan;
    if (type->tp_new == struct_new && type->tp_init == struct_init && n <= FAST_MAX_FIELDS) {
        type->tp_vectorcall = struct_vectorcall;
    }
    Py_RETURN_NONE;

fail:
    plan_free(plan);
    return NULL;
}

#undef E_NAME
#undef E_ALIAS
#undef E_SPEC
#undef E_DEFAULT
#undef E_CONSTANT
