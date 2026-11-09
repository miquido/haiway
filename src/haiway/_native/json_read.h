/* ------------------------------------------------------------ json decoding */

/*
 * An instance read from JSON without the intermediate Python containers, validated as
 * it is read. The decoder is not the authority on what is wrong with a document: a
 * syntax error, a key which is not an attribute, a value which does not validate - any
 * of them makes it decline, and the standard library reads the document again and
 * raises the error with its position. A document which is right is read once.
 *
 * What runs code of the user - a validator, a verifier, a class validating itself, a
 * default which is computed - is kept pending and run once the whole document is known
 * to be right, so a document refused for something else never runs it.
 */

typedef struct {
    PyObject *owner;     /* the instance the value belongs to */
    Py_ssize_t index;
    Spec *spec;
    PyObject *value;     /* what was read, NULL for the default */
} Pending;

typedef struct {
    ModuleState *state;
    const char *p;
    const char *end;
    Pending *pending;
    Py_ssize_t pending_count;
    Py_ssize_t pending_capacity;
    int depth;
    int limit;  /* how deep a document goes, which is as far as Python recurses */
} Decoder;

static int
add_pending(Decoder *d, PyObject *owner, Py_ssize_t index, Spec *spec, PyObject *value)
{
    if (d->pending_count == d->pending_capacity) {
        Py_ssize_t capacity = d->pending_capacity == 0 ? 8 : d->pending_capacity * 2;
        Pending *grown = PyMem_Realloc(d->pending, (size_t)capacity * sizeof(Pending));
        if (grown == NULL) {
            PyErr_NoMemory();
            return 0;
        }
        d->pending = grown;
        d->pending_capacity = capacity;
    }
    Pending *item = &d->pending[d->pending_count++];
    item->owner = owner;
    item->index = index;
    item->spec = spec;
    item->value = value;
    return 1;
}

static void
release_pending(Decoder *d)
{
    for (Py_ssize_t i = 0; i < d->pending_count; i++) Py_XDECREF(d->pending[i].value);
    PyMem_Free(d->pending);
    d->pending = NULL;
    d->pending_count = 0;
}

static inline void
skip_whitespace(Decoder *d)
{
    while (d->p < d->end && (*d->p == ' ' || *d->p == '\t' || *d->p == '\n' || *d->p == '\r')) d->p++;
}

/* NULL without an error set is the decoder declining the input - NULL with one is a failure */
static PyObject *decode_any(Decoder *d);
static PyObject *decode_spec(Decoder *d, Spec *spec);

/* a failed validation declines - the standard library reads it again, which reports it with its position */
static PyObject *
decline_failure(void)
{
    if (PyErr_ExceptionMatches(PyExc_Exception)) PyErr_Clear();
    return NULL;
}

/* the bytes of a string, up to its closing quote - 0 when it is not one this reads */
static int
string_span(Decoder *d, const char **start, Py_ssize_t *length, int *escaped)
{
    if (d->p >= d->end || *d->p != '"') return 0;
    d->p++;
    *start = d->p;
    *escaped = 0;
    while (d->p < d->end) {
        unsigned char c = (unsigned char)*d->p;
        if (c == '"') {
            *length = d->p - *start;
            d->p++;
            return 1;
        }
        if (c < 0x20) return 0;  /* a control character is not allowed unescaped */
        if (c == '\\') {
            *escaped = 1;
            d->p++;
            if (d->p >= d->end) return 0;
        }
        d->p++;
    }
    return 0;
}

/* the text of a string as the document spells it - declined when it is not UTF-8 */
static PyObject *
decoded_span(const char *start, Py_ssize_t length)
{
    PyObject *text = PyUnicode_DecodeUTF8(start, length, NULL);
    if (text == NULL && PyErr_ExceptionMatches(PyExc_UnicodeDecodeError)) PyErr_Clear();  /* json.loads says what is wrong */
    return text;
}

/* a string with escapes resolved - NULL without an error when one of them is not valid */
static PyObject *
unescape_string(PyObject *escaped)
{
    Py_ssize_t length = PyUnicode_GET_LENGTH(escaped);
    int kind = PyUnicode_KIND(escaped);
    const void *data = PyUnicode_DATA(escaped);
    Py_UCS4 *buffer = PyMem_Malloc((size_t)(length + 1) * sizeof(Py_UCS4));
    if (buffer == NULL) return PyErr_NoMemory();
    Py_ssize_t count = 0;
    Py_ssize_t p = 0;
    while (p < length) {
        Py_UCS4 c = PyUnicode_READ(kind, data, p);
        if (c != '\\') {
            buffer[count++] = c;
            p++;
            continue;
        }
        p++;  /* the escape, which is known to have a character after it */
        switch (PyUnicode_READ(kind, data, p)) {
        case '"': buffer[count++] = '"'; p++; break;
        case '\\': buffer[count++] = '\\'; p++; break;
        case '/': buffer[count++] = '/'; p++; break;
        case 'b': buffer[count++] = '\b'; p++; break;
        case 'f': buffer[count++] = '\f'; p++; break;
        case 'n': buffer[count++] = '\n'; p++; break;
        case 'r': buffer[count++] = '\r'; p++; break;
        case 't': buffer[count++] = '\t'; p++; break;
        case 'u': {
            Py_UCS4 point = 0;
            if (length - p < 5) goto invalid;
            for (int i = 1; i <= 4; i++) {
                Py_UCS4 h = PyUnicode_READ(kind, data, p + i);
                point <<= 4;
                if (h >= '0' && h <= '9') point |= h - '0';
                else if (h >= 'a' && h <= 'f') point |= h - 'a' + 10;
                else if (h >= 'A' && h <= 'F') point |= h - 'A' + 10;
                else goto invalid;
            }
            p += 5;
            if (point >= 0xd800 && point <= 0xdbff && length - p >= 6
                && PyUnicode_READ(kind, data, p) == '\\' && PyUnicode_READ(kind, data, p + 1) == 'u') {
                Py_UCS4 low = 0;
                int valid = 1;
                for (int i = 2; i <= 5 && valid; i++) {
                    Py_UCS4 h = PyUnicode_READ(kind, data, p + i);
                    low <<= 4;
                    if (h >= '0' && h <= '9') low |= h - '0';
                    else if (h >= 'a' && h <= 'f') low |= h - 'a' + 10;
                    else if (h >= 'A' && h <= 'F') low |= h - 'A' + 10;
                    else valid = 0;
                }
                if (valid && low >= 0xdc00 && low <= 0xdfff) {
                    point = 0x10000 + (((point - 0xd800) << 10) | (low - 0xdc00));
                    p += 6;
                }
            }
            buffer[count++] = point;  /* a lone surrogate is kept, as json.loads keeps it */
            break;
        }
        default:
            goto invalid;
        }
    }
    PyObject *text = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, buffer, count);
    PyMem_Free(buffer);
    return text;
invalid:
    PyMem_Free(buffer);
    return NULL;
}

static PyObject *
decode_string(Decoder *d)
{
    const char *start;
    Py_ssize_t length;
    int escaped;
    if (!string_span(d, &start, &length, &escaped)) return NULL;
    PyObject *text = decoded_span(start, length);
    if (text == NULL || !escaped) return text;
    PyObject *unescaped = unescape_string(text);
    Py_DECREF(text);
    return unescaped;
}

/* the attribute a key names, told by its bytes where they are spelled plainly - -1 for none */
static Py_ssize_t
field_for_span(CompiledPlan *plan, const char *start, Py_ssize_t length, int escaped)
{
    if (!escaped) return field_for_bytes(plan, start, length);
    PyObject *key = decoded_span(start, length);
    if (key != NULL && escaped) {
        PyObject *unescaped = unescape_string(key);
        Py_DECREF(key);
        key = unescaped;
    }
    if (key == NULL) return -1;
    Py_ssize_t index = field_for_key(plan, key);
    Py_DECREF(key);
    return index;
}

static PyObject *
decode_number(Decoder *d)
{
    const char *start = d->p;
    const char *p = d->p;
    int is_float = 0;
    if (p < d->end && *p == '-') p++;
    if (p >= d->end) return NULL;
    if (*p == '0') {
        p++;
        if (p < d->end && *p >= '0' && *p <= '9') return NULL;  /* no leading zeros */
    } else if (*p >= '1' && *p <= '9') {
        while (p < d->end && *p >= '0' && *p <= '9') p++;
    } else {
        return NULL;
    }
    if (p < d->end && *p == '.') {
        const char *digits = ++p;
        while (p < d->end && *p >= '0' && *p <= '9') p++;
        if (p == digits) return NULL;
        is_float = 1;
    }
    if (p < d->end && (*p == 'e' || *p == 'E')) {
        p++;
        if (p < d->end && (*p == '+' || *p == '-')) p++;
        const char *digits = p;
        while (p < d->end && *p >= '0' && *p <= '9') p++;
        if (p == digits) return NULL;
        is_float = 1;
    }
    Py_ssize_t length = p - start;
    if (!is_float && length <= 18) {  /* what fits a machine word for sure, read in place */
        const char *q = start;
        int negative = *q == '-';
        if (negative) q++;
        long long magnitude = 0;
        for (; q < p; q++) magnitude = magnitude * 10 + (*q - '0');
        d->p = p;
        return PyLong_FromLongLong(negative ? -magnitude : magnitude);
    }
    char small[64];
    char *text = length < (Py_ssize_t)sizeof(small) ? small : PyMem_Malloc((size_t)length + 1);
    if (text == NULL) return PyErr_NoMemory();
    memcpy(text, start, (size_t)length);
    text[length] = '\0';
    PyObject *number;
    if (is_float) {
        double parsed = PyOS_string_to_double(text, NULL, NULL);
        number = (parsed == -1.0 && PyErr_Occurred()) ? NULL : PyFloat_FromDouble(parsed);
    } else {
        number = PyLong_FromString(text, NULL, 10);
    }
    if (text != small) PyMem_Free(text);
    d->p = p;
    return number;
}

static int
match_word(Decoder *d, const char *word, Py_ssize_t length)
{
    if (d->end - d->p < length || memcmp(d->p, word, (size_t)length) != 0) return 0;
    d->p += length;
    return 1;
}

static PyObject *
decode_array(Decoder *d)
{
    d->p++;  /* [ */
    PyObject *list = PyList_New(0);
    if (list == NULL) return NULL;
    skip_whitespace(d);
    if (d->p < d->end && *d->p == ']') {
        d->p++;
        return list;
    }
    for (;;) {
        skip_whitespace(d);
        PyObject *element = decode_any(d);
        if (element == NULL || PyList_Append(list, element) < 0) {
            Py_XDECREF(element);
            Py_DECREF(list);
            return NULL;
        }
        Py_DECREF(element);
        skip_whitespace(d);
        if (d->p < d->end && *d->p == ',') {
            d->p++;
            continue;
        }
        if (d->p < d->end && *d->p == ']') {
            d->p++;
            return list;
        }
        Py_DECREF(list);
        return NULL;
    }
}

static PyObject *
decode_object(Decoder *d)
{
    d->p++;  /* { */
    PyObject *dict = PyDict_New();
    if (dict == NULL) return NULL;
    skip_whitespace(d);
    if (d->p < d->end && *d->p == '}') {
        d->p++;
        return dict;
    }
    for (;;) {
        skip_whitespace(d);
        PyObject *key = decode_string(d);
        if (key == NULL) goto fail;
        skip_whitespace(d);
        if (d->p >= d->end || *d->p != ':') {
            Py_DECREF(key);
            goto fail;
        }
        d->p++;
        skip_whitespace(d);
        PyObject *value = decode_any(d);
        if (value == NULL || PyDict_SetItem(dict, key, value) < 0) {  /* the last of a repeated key stays */
            Py_DECREF(key);
            Py_XDECREF(value);
            goto fail;
        }
        Py_DECREF(key);
        Py_DECREF(value);
        skip_whitespace(d);
        if (d->p < d->end && *d->p == ',') {
            d->p++;
            continue;
        }
        if (d->p < d->end && *d->p == '}') {
            d->p++;
            return dict;
        }
        goto fail;
    }
fail:
    Py_DECREF(dict);
    return NULL;
}

static PyObject *
decode_any_unbounded(Decoder *d)
{
    if (d->p >= d->end) return NULL;
    switch (*d->p) {
    case '{': return decode_object(d);
    case '[': return decode_array(d);
    case '"': return decode_string(d);
    case 't': return match_word(d, "true", 4) ? Py_NewRef(Py_True) : NULL;
    case 'f': return match_word(d, "false", 5) ? Py_NewRef(Py_False) : NULL;
    case 'n': return match_word(d, "null", 4) ? Py_NewRef(Py_None) : NULL;
    case 'N': return match_word(d, "NaN", 3) ? PyFloat_FromDouble(Py_NAN) : NULL;
    case 'I': return match_word(d, "Infinity", 8) ? PyFloat_FromDouble(Py_HUGE_VAL) : NULL;
    case '-':
        if (match_word(d, "-Infinity", 9)) return PyFloat_FromDouble(-Py_HUGE_VAL);
        return decode_number(d);
    default:
        return decode_number(d);
    }
}

static PyObject *
decode_any(Decoder *d)
{
    /* deeper than Python goes is for json.loads, which says so */
    if (d->depth >= d->limit) return NULL;
    if (Py_EnterRecursiveCall(" while decoding json")) return NULL;
    d->depth++;
    PyObject *result = decode_any_unbounded(d);
    d->depth--;
    Py_LeaveRecursiveCall();
    return result;
}

static PyObject *decode_struct_unbounded(Decoder *d, PyTypeObject *type);

static PyObject *
decode_struct(Decoder *d, PyTypeObject *type)
{
    if (d->depth >= d->limit) return NULL;
    if (Py_EnterRecursiveCall(" while decoding json")) return NULL;
    d->depth++;
    PyObject *result = decode_struct_unbounded(d, type);
    d->depth--;
    Py_LeaveRecursiveCall();
    return result;
}

/* an instance read from an object - declined before anything is consumed when the class
 * is not read here, declined after when the object does not fit it */
static PyObject *
decode_struct_unbounded(Decoder *d, PyTypeObject *type)
{
    CompiledPlan *plan = PLAN_OF(type);
    if (plan == NULL || type->tp_new != struct_new || type->tp_init != struct_init) return NULL;
    if (d->p >= d->end || *d->p != '{') return NULL;
    d->p++;

    PyObject *self = type->tp_alloc(type, 0);
    if (self == NULL) return NULL;
    PyObject **items = ITEMS(self);
    char stack[FAST_MAX_FIELDS] = {0};
    char *given = plan->count <= FAST_MAX_FIELDS ? stack : PyMem_Calloc((size_t)plan->count, 1);
    if (given == NULL) {
        Py_DECREF(self);
        return PyErr_NoMemory();
    }
    int deferred = 0;  /* whether a value of this instance is bound later */

    skip_whitespace(d);
    if (d->p < d->end && *d->p == '}') {
        d->p++;
    } else {
        for (;;) {
            skip_whitespace(d);
            const char *key_start;
            Py_ssize_t key_length;
            int key_escaped;
            if (!string_span(d, &key_start, &key_length, &key_escaped)) goto fail;
            Py_ssize_t index = field_for_span(plan, key_start, key_length, key_escaped);
            if (index < 0 || given[index]) goto fail;  /* not an attribute, or given twice */
            given[index] = 1;
            skip_whitespace(d);
            if (d->p >= d->end || *d->p != ':') goto fail;
            d->p++;
            skip_whitespace(d);

            FieldPlan *field = &plan->fields[index];
            if (field->spec->pure) {
                PyObject *value = decode_spec(d, field->spec);
                if (value == NULL) goto fail;
                items[index] = value;
            } else {
                /* code of the user validates - once the document is known to be right */
                PyObject *raw = decode_any(d);
                if (raw == NULL) goto fail;
                if (!add_pending(d, self, index, field->spec, raw)) {
                    Py_DECREF(raw);
                    goto fail;
                }
                deferred = 1;
            }

            skip_whitespace(d);
            if (d->p < d->end && *d->p == ',') {
                d->p++;
                continue;
            }
            if (d->p < d->end && *d->p == '}') {
                d->p++;
                break;
            }
            goto fail;
        }
    }

    for (Py_ssize_t i = 0; i < plan->count; i++) {
        if (given[i]) continue;
        FieldPlan *field = &plan->fields[i];
        if (field->cached != NULL) {
            items[i] = Py_NewRef(field->cached);
        } else {
            /* the default is computed - once the document is known to be right */
            if (!add_pending(d, self, i, NULL, NULL)) goto fail;
            deferred = 1;
        }
    }
    if (given != stack) PyMem_Free(given);
    if (!deferred) finish_construction(self);
    return self;

fail:
    if (given != stack) PyMem_Free(given);
    Py_DECREF(self);
    return NULL;
}

/* an array read as a tuple of what its elements are accepted as - the elements are gathered
 * on the stack, or in memory growing past it, and the tuple is made once, at their count */
#define SHORT_ARRAY 32

static PyObject *
decode_sequence(Decoder *d, Spec *element_spec)
{
    d->p++;  /* [ */
    PyObject *stack[SHORT_ARRAY];
    PyObject **elements = stack;
    Py_ssize_t capacity = SHORT_ARRAY;
    Py_ssize_t count = 0;
    skip_whitespace(d);
    if (d->p < d->end && *d->p == ']') {
        d->p++;
        return PyTuple_New(0);
    }
    for (;;) {
        skip_whitespace(d);
        PyObject *element = decode_spec(d, element_spec);
        if (element == NULL) goto fail;
        if (count == capacity) {
            capacity *= 2;
            PyObject **grown = elements == stack ? PyMem_Malloc((size_t)capacity * sizeof(PyObject *))
                                                 : PyMem_Realloc(elements, (size_t)capacity * sizeof(PyObject *));
            if (grown == NULL) {
                Py_DECREF(element);
                PyErr_NoMemory();
                goto fail;
            }
            if (elements == stack) memcpy(grown, stack, sizeof(stack));
            elements = grown;
        }
        elements[count++] = element;
        skip_whitespace(d);
        if (d->p < d->end && *d->p == ',') {
            d->p++;
            continue;
        }
        if (d->p < d->end && *d->p == ']') {
            d->p++;
            break;
        }
        goto fail;
    }
    PyObject *tuple = PyTuple_New(count);
    if (tuple == NULL) goto fail;
    for (Py_ssize_t i = 0; i < count; i++) PyTuple_SET_ITEM(tuple, i, elements[i]);
    if (elements != stack) PyMem_Free(elements);
    return tuple;

fail:
    for (Py_ssize_t i = 0; i < count; i++) Py_DECREF(elements[i]);
    if (elements != stack) PyMem_Free(elements);
    return NULL;
}

/* an object read as the Map of what its keys and values are accepted as */
static PyObject *
decode_mapping(Decoder *d, Spec *spec)
{
    d->p++;  /* { */
    PyObject *dict = PyDict_New();
    if (dict == NULL) return NULL;
    skip_whitespace(d);
    if (d->p < d->end && *d->p == '}') {
        d->p++;
    } else {
        for (;;) {
            skip_whitespace(d);
            PyObject *key = decode_string(d);
            if (key == NULL) goto fail;
            PyObject *checked_key = validate_spec(d->state, spec->children[0], key);
            Py_DECREF(key);
            if (checked_key == NULL) {
                decline_failure();
                goto fail;
            }
            skip_whitespace(d);
            if (d->p >= d->end || *d->p != ':') {
                Py_DECREF(checked_key);
                goto fail;
            }
            d->p++;
            skip_whitespace(d);
            PyObject *value = decode_spec(d, spec->children[1]);
            if (value == NULL || PyDict_SetItem(dict, checked_key, value) < 0) {  /* the last of a repeated key stays */
                Py_DECREF(checked_key);
                Py_XDECREF(value);
                goto fail;
            }
            Py_DECREF(checked_key);
            Py_DECREF(value);
            skip_whitespace(d);
            if (d->p < d->end && *d->p == ',') {
                d->p++;
                continue;
            }
            if (d->p < d->end && *d->p == '}') {
                d->p++;
                break;
            }
            goto fail;
        }
    }
    PyObject *mapped = PyObject_CallOneArg(d->state->map, dict);
    Py_DECREF(dict);
    return mapped;

fail:
    Py_DECREF(dict);
    return NULL;
}

/* whether a union has an alternative of the kind */
static inline int
union_has(Spec *spec, int kind)
{
    for (Py_ssize_t i = 0; i < spec->count; i++) {
        if (spec->children[i]->kind == kind) return 1;
    }
    return 0;
}

/* a value read the way the spec accepts it - validated as it is read */
static PyObject *
decode_spec(Decoder *d, Spec *spec)
{
    if (d->p >= d->end) return NULL;
    /* the shapes read directly - only when nothing of the user runs on the raw value */
    if (spec->validating == NULL && spec->verifying == NULL) {
        switch (spec->kind) {
        case SPEC_STATE:
            if (*d->p == '{') {
                const char *start = d->p;
                PyObject *instance = decode_struct(d, (PyTypeObject *)spec->base);
                if (instance != NULL || PyErr_Occurred() || d->p != start) return instance;
            }
            break;
        case SPEC_UNION:
            if (*d->p == 'n' && union_has(spec, SPEC_NONE)) {
                return match_word(d, "null", 4) ? Py_NewRef(Py_None) : NULL;
            }
            if (*d->p == '{') {
                /* the first class the object fits - the alternatives are tried in order */
                for (Py_ssize_t i = 0; i < spec->count; i++) {
                    Spec *alternative = spec->children[i];
                    if (alternative->kind != SPEC_STATE || alternative->validating != NULL || alternative->verifying != NULL) continue;
                    const char *start = d->p;
                    PyObject *instance = decode_struct(d, (PyTypeObject *)alternative->base);
                    if (instance != NULL || PyErr_Occurred()) return instance;
                    d->p = start;
                }
            }
            break;
        case SPEC_SEQUENCE:
            if (*d->p == '[') return decode_sequence(d, spec->children[0]);
            break;
        case SPEC_MAPPING:
            if (*d->p == '{') return decode_mapping(d, spec);
            break;
        default:
            break;
        }
    }

    /* anything else is read as it is and validated */
    PyObject *raw = decode_any(d);
    if (raw == NULL) return NULL;
    PyObject *validated = validate_spec(d->state, spec, raw);
    Py_DECREF(raw);
    return validated != NULL ? validated : decline_failure();
}

/* what was kept pending, in the order it was read - 0 declining when any of it fails */
static int
apply_pending(Decoder *d)
{
    for (Py_ssize_t i = 0; i < d->pending_count; i++) {
        Pending *item = &d->pending[i];
        PyObject *validated;
        if (item->value != NULL) {
            validated = validate_spec(d->state, item->spec, item->value);
        } else {
            validated = field_default(&PLAN_OF(Py_TYPE(item->owner))->fields[item->index]);
        }
        if (validated == NULL) {
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return -1;
            decline_failure();
            return 0;
        }
        PyObject **slot = &ITEMS(item->owner)[item->index];
        Py_XSETREF(*slot, validated);
    }
    /* the instances bound late are let go of by the collector now, where they can be */
    for (Py_ssize_t i = 0; i < d->pending_count; i++) finish_construction(d->pending[i].owner);
    return 1;
}

/*
 * The text of a json document, read in place - NULL without an error when it is not one
 * this reads. Bytes are read as UTF-8, which is what json.loads reads without a mark of
 * another encoding; the strings of the document are decoded one by one, and one which
 * is not UTF-8 declines, so anything else is for json.loads to detect.
 */
static const char *
document_text(PyObject *value, Py_ssize_t *length)
{
    if (PyUnicode_Check(value)) {
        if (PyUnicode_IS_ASCII(value)) {  /* as it is - no UTF-8 of it is kept on the str */
            *length = PyUnicode_GET_LENGTH(value);
            return PyUnicode_DATA(value);
        }
        return PyUnicode_AsUTF8AndSize(value, length);
    }
    if (PyBytes_Check(value)) {
        *length = PyBytes_GET_SIZE(value);
        return PyBytes_AS_STRING(value);
    }
    return NULL;
}

/* from_json(cls, text, array) - an instance, or None when the standard library has to read it */
static PyObject *
from_json_native(PyObject *mod, PyObject *args)
{
    ModuleState *state = module_state(mod);
    PyObject *cls;
    PyObject *text;
    int array;
    if (!PyArg_ParseTuple(args, "OOp", &cls, &text, &array)) return NULL;
    if (!PyType_Check(cls) || !PyType_IsSubtype((PyTypeObject *)cls, state->struct_type)) {
        PyErr_SetString(PyExc_TypeError, "from_json expects a Struct class");
        return NULL;
    }

    Py_ssize_t length;
    const char *data = document_text(text, &length);
    if (data == NULL) {
        if (PyErr_Occurred()) {
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
            PyErr_Clear();  /* not valid text - json.loads says so */
        }
        Py_RETURN_NONE;
    }

    Decoder decoder = {state, data, data + length, NULL, 0, 0, 0, Py_GetRecursionLimit()};
    PyObject *result = NULL;
    skip_whitespace(&decoder);
    if (!array) {
        result = decode_struct(&decoder, (PyTypeObject *)cls);
    } else if (decoder.p < decoder.end && *decoder.p == '[') {
        decoder.p++;
        PyObject *list = PyList_New(0);
        if (list != NULL) {
            skip_whitespace(&decoder);
            int closed = 0;
            if (decoder.p < decoder.end && *decoder.p == ']') {
                decoder.p++;
                closed = 1;
            }
            while (!closed) {
                skip_whitespace(&decoder);
                PyObject *element = decode_struct(&decoder, (PyTypeObject *)cls);
                if (element == NULL || PyList_Append(list, element) < 0) {
                    Py_XDECREF(element);
                    Py_CLEAR(list);
                    break;
                }
                Py_DECREF(element);
                skip_whitespace(&decoder);
                if (decoder.p < decoder.end && *decoder.p == ',') {
                    decoder.p++;
                    continue;
                }
                if (decoder.p < decoder.end && *decoder.p == ']') {
                    decoder.p++;
                    closed = 1;
                    continue;
                }
                Py_CLEAR(list);
                break;
            }
            if (list != NULL) {
                result = PyList_AsTuple(list);
                Py_DECREF(list);
            }
        }
    }

    if (result != NULL) {
        skip_whitespace(&decoder);
        if (decoder.p != decoder.end) Py_CLEAR(result);  /* something after the document */
    }
    if (result != NULL && apply_pending(&decoder) <= 0) Py_CLEAR(result);
    release_pending(&decoder);
    if (result == NULL && !PyErr_Occurred()) Py_RETURN_NONE;
    return result;
}
