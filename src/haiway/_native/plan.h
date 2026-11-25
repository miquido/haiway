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

/* what is told of code of the user running while a value is read: never, or not told yet */
enum {
    PURITY_UNKNOWN = 0,
    PURITY_PURE,
    PURITY_IMPURE,
};

/* what is known of the default of an attribute - each is a refinement of the one before it */
typedef enum {
    DEFAULT_COMPUTED,        /* a different value may come every time */
    DEFAULT_CONSTANT,        /* the very same value every time, not kept */
    DEFAULT_CACHED_ACYCLIC,  /* constant and kept validated, which can never be part of a cycle */
    DEFAULT_CACHED_CYCLIC    /* constant and kept validated, which may be part of one */
} DefaultKind;

typedef struct {
    DefaultKind kind;
    PyObject *value;  /* the validated default - NULL exactly when `kind` is not one of the cached, set by `cache_default` only */
} DefaultState;

typedef struct {
    PyObject *name;
    PyObject *alias;  /* NULL when there is none */
    const char *name_utf8;   /* the spellings json keys are compared with, in place */
    Py_ssize_t name_length;
    const char *alias_utf8;  /* NULL when there is no alias */
    Py_ssize_t alias_length;
    const char *json_key;    /* the key as json spells it, with the colon - `"key":` */
    Py_ssize_t json_key_length;
    Spec *spec;       /* owned by the spec object the plan tuple holds */
    PyObject *default_value;  /* () -> the validated default */
    DefaultState default_state;
    int pure;         /* PURITY_* of the spec of the attribute - told when first asked, see `field_pure` */
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
    int validates_itself;  /* whether the class declares a `validate` of its own - a document is not read around it */
    int native_copy;  /* whether `__deepcopy__` is the native one - a class overriding it is copied through it */
    int pure;  /* PURITY_* of the class - told when first asked, see `plan_pure` */
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

static void
key_slots_add(CompiledPlan *plan, const char *text, Py_ssize_t length, Py_ssize_t index)
{
    size_t at = key_hash(text, length) & plan->key_mask;
    while (plan->key_slots[at].text != NULL) at = (at + 1) & plan->key_mask;
    plan->key_slots[at].text = text;
    plan->key_slots[at].length = length;
    plan->key_slots[at].index = index;
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

/* the error of an instance with no value bound - one made with `__new__` and not initialized yet,
 * which is not one holding Missing: it is never read as that */
static void
not_initialized(PyTypeObject *type)
{
    PyErr_Format(PyExc_TypeError, "%s is not initialized", type->tp_name);
}

/* the compiled plan of a class, or the error of there being none */
static inline CompiledPlan *
plan_or_error(PyTypeObject *type)
{
    CompiledPlan *plan = PLAN_OF(type);
    if (plan == NULL) PyErr_Format(PyExc_TypeError, "%s was never given a plan", type->tp_name);
    return plan;
}

static inline PyObject *
validate_field(FieldPlan *field, CompiledPlan *plan, PyObject *value, int ignore_extra)
{
    return validate_spec(plan->state, field->spec, value, ignore_extra);
}

static inline int
field_constant(const FieldPlan *field)
{
    return field->default_state.kind != DEFAULT_COMPUTED;
}

/* the validated default kept for the attribute - NULL when it is not kept */
static inline PyObject *
field_cached(const FieldPlan *field)
{
    return field->default_state.value;
}

/* whether the value is the cached default, which can never be part of a cycle */
static inline int
is_cached_acyclic(const FieldPlan *field, PyObject *value)
{
    return field->default_state.kind == DEFAULT_CACHED_ACYCLIC && value == field->default_state.value;
}

static inline void
cache_default(FieldPlan *field, PyObject *validated, int acyclic)
{
    field->default_state.kind = acyclic ? DEFAULT_CACHED_ACYCLIC : DEFAULT_CACHED_CYCLIC;
    field->default_state.value = validated;
}

/* the default of an attribute which was not given, validated */
static inline PyObject *
field_default(FieldPlan *field)
{
    if (field_cached(field) != NULL) return Py_NewRef(field_cached(field));
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
    PyObject *exc = take_to_report();
    if (exc == NULL) return;  /* not ours to wrap */
    if (kwargs != NULL) {
        PyObject *r = PyObject_CallFunctionObjArgs(state->check, (PyObject *)type, kwargs, NULL);
        if (r == NULL) {
            Py_DECREF(exc);
            return;
        }
        Py_DECREF(r);
    }
    report_taken(state, exc, PyUnicode_FromFormat(".%U", name));
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
 * What the garbage collector has to look at. A value can be part of a cycle when it is
 * tracked, or when it is untracked for now but can become tracked later - which only
 * the containers which can never change are safe of: an exact tuple, frozenset,
 * frozendict, Map or Meta, and an instance, each untracked only once nothing it
 * holds can be part of a cycle. That is the rule of CPython itself, for its tuples.
 */
static inline int
is_frozen_container(ModuleState *state, PyObject *value)
{
    return PyTuple_CheckExact(value) || PyFrozenSet_CheckExact(value) || PyFrozenDict_CheckExact(value)
        || Py_IS_TYPE(value, (PyTypeObject *)state->map) || Py_IS_TYPE(value, (PyTypeObject *)state->meta);
}

/* the rule for a value the collector tracks no longer, or never did */
static inline int
untracked_may_join_cycle(ModuleState *state, PyObject *value)
{
    /* an instance is untracked only by `finish_construction`, and never changes after */
    return !(is_frozen_container(state, value) || is_struct(state, value));
}

/* how deep `settle` looks into nested containers - what is deeper stays tracked */
#define SETTLE_DEPTH 32

/*
 * Whether a value can be part of a cycle, untracking on the way the frozen containers
 * holding nothing which can - so that an instance holding them is untracked as well,
 * rather than every one of them waiting for the collector to find that out. No code of
 * the user runs here. Without the GIL only a container nothing else holds is untracked,
 * as the collector alone changes the tracking of what other threads can reach.
 */
static int settle_tracked(ModuleState *state, PyObject *value, int depth);

/* what a UUID refers to - its class, an int, which the collector does not track, and a
 * member of SafeUUID; anything else stops the traversal */
typedef struct {
    PyObject *type;
    PyObject *safe_uuid;
} UUIDReferents;

static int
visit_uuid_referent(PyObject *referent, void *arg)
{
    UUIDReferents *expected = (UUIDReferents *)arg;
    if (!PyObject_IS_GC(referent) || referent == expected->type) return 0;
    return Py_IS_TYPE(referent, (PyTypeObject *)expected->safe_uuid) ? 0 : 1;
}

/*
 * A UUID is tracked by the collector, being an instance of a class with slots - while it
 * holds its number and whether it is safe, a member of SafeUUID, which nothing it holds
 * can lead back from. What it holds is checked rather than assumed, as `object.__setattr__`
 * can rebind a slot of it. A subclass may hold anything else, so it is left as it is.
 */
static int
uuid_may_join_cycle(ModuleState *state, PyObject *value)
{
    UUIDReferents expected = {(PyObject *)Py_TYPE(value), state->safe_uuid};
    return Py_TYPE(value)->tp_traverse(value, visit_uuid_referent, &expected) != 0;
}

/* `settle` of a value which is not a scalar - those are checked inline, being most of them */
static int
settle_gc(ModuleState *state, PyObject *value, int depth)
{
    if (!PyObject_GC_IsTracked(value)) return untracked_may_join_cycle(state, value);
    if (Py_IS_TYPE(value, (PyTypeObject *)state->uuid)) return uuid_may_join_cycle(state, value);
    return settle_tracked(state, value, depth);
}

static inline int
settle(ModuleState *state, PyObject *value, int depth)
{
    return PyObject_IS_GC(value) && settle_gc(state, value, depth);
}

static int
settle_tracked(ModuleState *state, PyObject *value, int depth)
{
    if (!is_frozen_container(state, value) || depth >= SETTLE_DEPTH) return 1;
#ifdef Py_GIL_DISABLED
    if (Py_REFCNT(value) != 1) return 1;
#endif
    if (PyTuple_CheckExact(value)) {
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(value); i++) {
            if (settle(state, PyTuple_GET_ITEM(value, i), depth + 1)) return 1;
        }
    } else if (PyFrozenSet_CheckExact(value)) {
        PyObject *iterator = PyObject_GetIter(value);
        if (iterator == NULL) {
            PyErr_Clear();
            return 1;
        }
        PyObject *item;
        int cyclic = 0;
        while (!cyclic && (item = PyIter_Next(iterator)) != NULL) {
            cyclic = settle(state, item, depth + 1);
            Py_DECREF(item);
        }
        Py_DECREF(iterator);
        if (PyErr_Occurred()) {
            PyErr_Clear();
            return 1;
        }
        if (cyclic) return 1;
    } else {
        /* a frozendict, or a Map or a Meta - which are one, holding nothing else */
        PyObject *key;
        PyObject *item;
        Py_ssize_t position = 0;
        while (PyDict_Next(value, &position, &key, &item)) {
            if (settle(state, key, depth + 1) || settle(state, item, depth + 1)) return 1;
        }
    }
    PyObject_GC_UnTrack(value);
    return 0;
}

/*
 * An instance is tracked by the garbage collector from its allocation. One holding
 * nothing which can be part of a cycle never can be either - and since nothing can
 * ever be bound to it later, it is let go of for good once it is built.
 */
static inline void
finish_construction(PyObject *self)
{
    CompiledPlan *plan = PLAN_OF(Py_TYPE(self));
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject **items = ITEMS(self);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = items[i];
        if (value == NULL || is_cached_acyclic(&plan->fields[i], value)) continue;
        if (settle(plan->state, value, 0)) return;
    }
    PyObject_GC_UnTrack(self);
}

/*
 * An instance made of another by replacing some of its values. When the other is let
 * go of by the collector, nothing it holds can be part of a cycle - so only the values
 * which replaced its own are looked at.
 */
static inline void
finish_update(PyObject *self, PyObject *source)
{
    CompiledPlan *plan = PLAN_OF(Py_TYPE(self));
    if (PyObject_GC_IsTracked(source)) {
        finish_construction(self);
        return;
    }
    Py_ssize_t n = ITEM_COUNT(self);
    PyObject **items = ITEMS(self);
    PyObject **carried = ITEMS(source);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *value = items[i];
        if (value == NULL || value == carried[i]) continue;
        if (is_cached_acyclic(&plan->fields[i], value)) continue;
        if (settle(plan->state, value, 0)) return;
    }
    PyObject_GC_UnTrack(self);
}

static PyObject *struct_vectorcall(PyObject *callable, PyObject *const *args, size_t nargsf, PyObject *kwnames);
static PyObject *struct_new(PyTypeObject *type, PyObject *args, PyObject *kwargs);
static int struct_init(PyObject *obj, PyObject *args, PyObject *kwargs);

/* whether the class is constructed here - one declaring a `__new__` or an `__init__` of
 * its own takes over, whenever it is given one */
static inline int
constructs_natively(PyTypeObject *type)
{
    return type->tp_new == struct_new && type->tp_init == struct_init;
}

/*
 * Whether reading a value runs code of the user - a validator, a verifier, a class
 * validating itself, a default which is computed, a constructor of the class's own. What
 * is pure is read from a document as it is met, since nothing of the user can see it
 * refused later; the rest waits until the document is known to be right. This is the one
 * place telling it: a spec is pure when it runs no code of its own and nothing it holds
 * does, and a class when it is constructed here and every attribute is a pure spec with a
 * default which is the same every time - the two facts about an attribute are kept apart,
 * as a value which is given needs only the first, and one which is not given both. A class
 * refers to others, and to itself, so the classes already met are remembered - which is also
 * what lets a cycle end, taken as pure until a part of it says otherwise. Nothing is remembered
 * of a part which could not be told because a class has no plan yet, so it is asked again. A
 * graph reaching more classes than can be remembered is told impure, which is what is
 * remembered of it, as it was told no other way - and it is told once.
 */
#define PURITY_VISITED 64

typedef struct {
    PyTypeObject *types[PURITY_VISITED];
    Py_ssize_t count;
    int unplanned;  /* a class without a plan was met - the answer may change once it has one */
    int overflowed;  /* too many classes were met - everything not seen is taken as impure */
} Visited;

static int plan_pure(PyTypeObject *type, Visited *visited);

static int
spec_pure(Spec *spec, Visited *visited)
{
    if (spec->validating != NULL || spec->verifying != NULL) return 0;
    switch (spec->kind) {
    case SPEC_CUSTOM:
    case SPEC_CUSTOM_STATE:
    case SPEC_ALIAS:
        return 0;
    case SPEC_STATE:
        return plan_pure((PyTypeObject *)spec->base, visited);
    default:
        break;
    }
    for (Py_ssize_t i = 0; i < spec->count; i++) {
        if (!spec_pure(spec->children[i], visited)) return 0;
    }
    return 1;
}

static int
plan_pure(PyTypeObject *type, Visited *visited)
{
    CompiledPlan *plan = PLAN_OF(type);
    if (plan != NULL) {
        int known = LOAD_RELAXED(&plan->pure);
        if (known != PURITY_UNKNOWN) return known == PURITY_PURE;
    }
    for (Py_ssize_t i = 0; i < visited->count; i++) {
        if (visited->types[i] == type) return 1;
    }
    if (plan == NULL) {
        visited->unplanned = 1;
        return 0;
    }
    if (visited->count == PURITY_VISITED) {
        visited->overflowed = 1;
        return 0;
    }
    int root = visited->count == 0;
    visited->types[visited->count++] = type;
    int pure = constructs_natively(type);
    for (Py_ssize_t i = 0; pure && i < plan->count; i++) {
        pure = field_constant(&plan->fields[i]) && spec_pure(plan->fields[i].spec, visited);
    }
    /* an impure class is so for good, a pure one is only where it was asked about first,
     * as the others it met were taken as pure for the sake of the cycles. Where the walk was
     * cut short it is impure, which is told for the class it started from only - the others
     * were cut short by where they were met */
    if (!visited->unplanned && (root || (!pure && !visited->overflowed))) {
        STORE_RELAXED(&plan->pure, pure ? PURITY_PURE : PURITY_IMPURE);
    }
    return pure;
}

/* kept out of its callers - the classes it looks at are remembered in a frame of their own,
 * which would otherwise be part of every level a document is read through */
static Py_NO_INLINE int
field_pure_told(FieldPlan *field)
{
    Visited visited = {{NULL}, 0, 0, 0};
    int pure = spec_pure(field->spec, &visited);
    if (!visited.unplanned) STORE_RELAXED(&field->pure, pure ? PURITY_PURE : PURITY_IMPURE);
    return pure;
}

/* whether validating a value given for an attribute of a class runs no code of the user -
 * told once. Whether its default is the same every time is `constant`, not told here */
static inline int
field_pure(FieldPlan *field)
{
    int known = LOAD_RELAXED(&field->pure);
    if (known != PURITY_UNKNOWN) return known == PURITY_PURE;
    return field_pure_told(field);
}

/*
 * Whether validating the default of an attribute of a class which is being given its plan
 * runs no code of the user - the class itself, which has no plan yet, is taken as pure, as it
 * is for the classes which refer to themselves - so the validated default can be kept - `declared` is the default
 * as it was declared. It does when the spec is pure, but also when the spec is not and the
 * default is accepted as it is, by an alternative which is a State or is pure, with every
 * alternative before it refusing it without code of the user: `None` or an instance for
 * `Checked | None`, whatever code `Checked` has to build one from a mapping. A default the
 * spec would have to convert is not, nor is one of a spec with code of its own on the value.
 * Nothing of the user is run to tell, and the declared default is never validated for it.
 */
static int
cacheable_default(ModuleState *state, PyTypeObject *type, Spec *spec, PyObject *declared)
{
    Visited visited = {{type}, 1, 0, 0};
    if (spec_pure(spec, &visited)) return 1;
    if (spec->validating != NULL || spec->verifying != NULL) return 0;
    PyObject *value = PyObject_CallNoArgs(declared);
    if (value == NULL) return PyErr_ExceptionMatches(PyExc_Exception) ? (PyErr_Clear(), 0) : -1;
    int accepted = 0;
    if (spec->kind == SPEC_STATE) {
        accepted = spec_check(state, spec, value);
        if (accepted < 0) {
            Py_DECREF(value);
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return -1;
            PyErr_Clear();
            return 0;
        }
    } else if (spec->kind == SPEC_UNION) {
        for (Py_ssize_t i = 0; i < spec->count && accepted == 0; i++) {
            Spec *alternative = spec->children[i];
            Visited alone = {{type}, 1, 0, 0};
            int plain = (alternative->kind == SPEC_STATE && alternative->validating == NULL
                         && alternative->verifying == NULL)
                || spec_pure(alternative, &alone);
            if (!plain) break;  /* what it checks, or does with the value, may be code of the user */
            int r = spec_check(state, alternative, value);
            if (r > 0) accepted = 1;
            else if (r < 0) break;
            else continue;
        }
        if (accepted == 0 && PyErr_Occurred()) {
            if (!PyErr_ExceptionMatches(PyExc_Exception)) {
                Py_DECREF(value);
                return -1;
            }
            PyErr_Clear();
        }
    }
    Py_DECREF(value);
    return accepted;
}

/* the entries of the plan handed over by Python, one for each attribute */
#define E_NAME(e) PyTuple_GET_ITEM((e), 0)
#define E_ALIAS(e) PyTuple_GET_ITEM((e), 1)
#define E_SPEC(e) PyTuple_GET_ITEM((e), 2)
#define E_DEFAULT(e) PyTuple_GET_ITEM((e), 3)
#define E_CONSTANT(e) PyTuple_GET_ITEM((e), 4)  /* what gives the default as it is declared, None unless it is the same every time */

/*
 * The values of an instance live right after its header, sized by its plan - which holds
 * only when nothing else gives the instance a layout. CPython takes the layout of a class
 * from `tp_base`, the one base adding the most to it, so that base has to be a Struct
 * itself, and the class must not add anything of its own: no __dict__, no __weakref__,
 * no slots, no variable size. The attributes of a base are read through its descriptors,
 * by position, so they have to be the first of the class, in the same order.
 */
static int
check_layout(ModuleState *state, PyTypeObject *type, PyObject *entries)
{
    PyTypeObject *base = type->tp_base;
    if (base == NULL || !PyType_IsSubtype(base, state->struct_type)) {
        PyErr_Format(
            PyExc_TypeError, "%s can't be combined with %s, which gives its instances a layout of its own",
            type->tp_name, base == NULL ? "nothing" : base->tp_name);
        return -1;
    }
    if (type->tp_basicsize != base->tp_basicsize || type->tp_itemsize != 0 || type->tp_dictoffset != 0
        || type->tp_weaklistoffset != 0
        || (type->tp_flags & (Py_TPFLAGS_MANAGED_DICT | Py_TPFLAGS_MANAGED_WEAKREF | Py_TPFLAGS_INLINE_VALUES))) {
        PyErr_Format(PyExc_TypeError, "%s can't have slots, a __dict__ or a __weakref__", type->tp_name);
        return -1;
    }
    CompiledPlan *inherited = PLAN_OF(base);
    if (inherited == NULL) {
        /* Struct itself - a class which failed to get a plan is no base */
        if (base->tp_basicsize == (Py_ssize_t)sizeof(PyObject)) return 0;
        PyErr_Format(PyExc_TypeError, "%s can't inherit from %s, which was never given a plan", type->tp_name, base->tp_name);
        return -1;
    }
    Py_ssize_t n = PyTuple_GET_SIZE(entries);
    int prefix = inherited->count <= n;
    for (Py_ssize_t i = 0; prefix && i < inherited->count; i++) {
        PyObject *name = E_NAME(PyTuple_GET_ITEM(entries, i));
        if (name == inherited->fields[i].name) continue;
        int same = PyUnicode_Compare(name, inherited->fields[i].name);
        if (same == -1 && PyErr_Occurred()) return -1;
        prefix = same == 0;
    }
    if (!prefix) {
        PyErr_Format(
            PyExc_TypeError, "%s has to keep the attributes of %s first, in the same order", type->tp_name,
            base->tp_name);
        return -1;
    }
    return 0;
}

/* the descriptors made for a plan which is let go of read it - so they are let go of as
 * well, keeping the error which is set */
static void
remove_descriptors(PyObject *cls, CompiledPlan *plan, PyObject *owned, Py_ssize_t count)
{
    PyObject *error = PyErr_GetRaisedException();
    for (Py_ssize_t k = 0; k < count; k++) {
        FieldPlan *field = &plan->fields[field_for_key(plan, PyTuple_GET_ITEM(owned, k))];
        if (PyObject_DelAttr(cls, field->name) < 0) PyErr_Clear();
    }
    PyErr_SetRaisedException(error);
}

/*
 * Whether a class declares a `validate` of its own, which is then what makes an instance of
 * a document - the usual one of a State is what the reader does. Told as `is_plain_state` of
 * Python tells it: by what the classes before `Struct` declare. A class nested in another
 * is told by its spec instead. Told once, when the class is given its plan.
 */
static int
validates_itself(ModuleState *state, PyTypeObject *type)
{
    PyObject *mro = Py_NewRef(type->tp_mro);
    int own = 0;
    for (Py_ssize_t i = 0; !own && i < PyTuple_GET_SIZE(mro); i++) {
        PyObject *base = PyTuple_GET_ITEM(mro, i);
        if (base == (PyObject *)state->struct_type) break;
        PyObject *declared = PyType_GetDict((PyTypeObject *)base);
        if (declared == NULL) {
            own = -1;
            break;
        }
        own = PyDict_Contains(declared, state->str_validate);
        Py_DECREF(declared);
    }
    Py_DECREF(mro);
    return own;
}

/*
 * compile_plan(cls, plan, owned) - the plan of the class as C data, kept on
 * the class, and a read-only descriptor on the class for each of the attributes it
 * declares itself (`owned`, a tuple of their names - the others read theirs through the
 * descriptors of a base). `plan` is a tuple with an entry for each attribute:
 *
 *   (name, alias, spec, default, constant)
 *
 * where `constant` is None, or what gives the default as it was declared when that is the
 * same every time.
 *
 * `spec` is the compiled description of the annotation, `default` what is called for
 * the validated default of an attribute which was not given, and `constant` whether it
 * is the same every time - the validated default of such an attribute is kept here when
 * the spec promises an immutable result with no code of the user involved. A default
 * which fails to validate is left to fail where the instance is created.
 * The size of an instance is decided here, so the class can't have been instantiated
 * before, nor be given a plan twice. The class is made immutable with it - the plan is
 * the last thing a class is given. The call of the class goes through the plan when
 * the class does not take over its construction.
 */
static PyObject *
compile_plan(PyObject *mod, PyObject *args)
{
    ModuleState *state = module_state(mod);
    PyObject *cls;
    PyObject *entries;
    PyObject *owned;
    if (!PyArg_ParseTuple(args, "OO!O!", &cls, &PyTuple_Type, &entries, &PyTuple_Type, &owned)) return NULL;
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
            || !Py_IS_TYPE(E_SPEC(entry), state->spec_type)
            || (E_CONSTANT(entry) != Py_None && !PyCallable_Check(E_CONSTANT(entry)))) {
            PyErr_SetString(PyExc_TypeError, "compile_plan expects entries of (name, alias, spec, default, constant)");
            return NULL;
        }
    }
    if (check_layout(state, type, entries) < 0) return NULL;

    CompiledPlan *plan = PyMem_RawMalloc(sizeof(CompiledPlan) + (size_t)n * sizeof(FieldPlan));
    if (plan == NULL) return PyErr_NoMemory();
    plan->state = state;
    plan->owner = Py_NewRef(entries);
    plan->names = NULL;
    plan->json_keys = NULL;
    plan->keys = NULL;
    plan->cached = NULL;
    plan->key_slots = NULL;
    plan->native_copy = 1;
    plan->pure = PURITY_UNKNOWN;
    plan->validates_itself = 0;
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
        field->default_state.kind = E_CONSTANT(entry) != Py_None ? DEFAULT_CONSTANT : DEFAULT_COMPUTED;
        field->default_state.value = NULL;
        field->pure = PURITY_UNKNOWN;
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
        /* a value is bound once, for good - by the constructors, never by the descriptor */
        field->member.flags = Py_READONLY;
        field->member.doc = NULL;

        PyObject *position = PyLong_FromSsize_t(i);
        if (position == NULL) goto fail;
        int failed = PyDict_SetItem(plan->keys, field->name, position) < 0
            || (field->alias != NULL && PyDict_SetItem(plan->keys, field->alias, position) < 0);
        Py_DECREF(position);
        if (failed) goto fail;

        int cacheable = field_constant(field) ? cacheable_default(state, type, field->spec, E_CONSTANT(entry)) : 0;
        if (cacheable < 0) goto fail;
        if (cacheable) {
            PyObject *validated = PyObject_CallNoArgs(field->default_value);
            if (validated == NULL) {
                if (!PyErr_ExceptionMatches(PyExc_Exception)) goto fail;
                PyErr_Clear();  /* left to fail where the instance is created */
            } else {
                failed = PyList_Append(plan->cached, validated) < 0;
                Py_DECREF(validated);
                if (failed) goto fail;
                cache_default(field, validated, !settle(state, validated, 0));  /* the list holds it */
            }
        }
    }

    plan->validates_itself = validates_itself(state, type);
    if (plan->validates_itself < 0) goto fail;

    /* a class with a `__deepcopy__` of its own - one returning itself, say - is copied
     * by it, also where it is held by another instance. looked up before the descriptors
     * are made, which read the plan - nothing fails once the class has them */
    PyObject *own_copy = PyObject_GetAttrString(cls, "__deepcopy__");
    PyObject *native_copy = own_copy == NULL ? NULL : PyObject_GetAttrString((PyObject *)state->struct_type, "__deepcopy__");
    if (native_copy == NULL) {
        Py_XDECREF(own_copy);
        goto fail;
    }
    plan->native_copy = own_copy == native_copy;
    Py_DECREF(own_copy);
    Py_DECREF(native_copy);

    /* the descriptors of the attributes declared here - a base declares the rest. what
     * is not the name of an attribute is refused before any descriptor is made */
    Py_ssize_t owned_count = PyTuple_GET_SIZE(owned);
    for (Py_ssize_t k = 0; k < owned_count; k++) {
        PyObject *name = PyTuple_GET_ITEM(owned, k);
        Py_ssize_t i = PyUnicode_Check(name) ? field_for_key(plan, name) : -1;
        if (i < 0 || PyUnicode_Compare(plan->fields[i].name, name) != 0) {
            PyErr_Format(PyExc_TypeError, "%R is not an attribute of %s", name, type->tp_name);
            goto fail;
        }
    }
    for (Py_ssize_t k = 0; k < owned_count; k++) {
        FieldPlan *field = &plan->fields[field_for_key(plan, PyTuple_GET_ITEM(owned, k))];
        PyObject *descriptor = PyDescr_NewMember(type, &field->member);
        int failed = descriptor == NULL || PyObject_SetAttr(cls, field->name, descriptor) < 0;
        Py_XDECREF(descriptor);
        if (!failed) continue;
        remove_descriptors(cls, plan, owned, k);
        goto fail;
    }

    /* the class is complete - nothing of it changes from here on, the way nothing of an
     * instance does: a descriptor replaced would hide a value, a constructor assigned
     * later would be one the class was not made for. the flag is set here rather than
     * by `PyType_Freeze`, which refuses a class with a base which can change - and
     * `typing.Generic` can. nothing else has seen the class yet */
    type->tp_flags |= Py_TPFLAGS_IMMUTABLETYPE;
    PyType_Modified(type);

    /* the size of an instance, and the call of the class - which tells by itself a class
     * taking over its construction, or one with more attributes than the fast path matches.
     * The class can be reached already, through `__subclasses__` or the collector - so what
     * a reader of the plan needs is written first, and the plan is published after it, the
     * call of the class last, which finds the plan itself */
    type->tp_basicsize = (Py_ssize_t)sizeof(PyObject) + n * (Py_ssize_t)sizeof(PyObject *);
    PUBLISH_PLAN(type, plan);
    PUBLISH_VECTORCALL(type, struct_vectorcall);
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
