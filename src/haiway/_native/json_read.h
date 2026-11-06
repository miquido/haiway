/* ------------------------------------------------------------ json decoding */

typedef struct {
    PyObject *owner;     /* the instance the value belongs to */
    Py_ssize_t index;
    FieldPlan *field;
    PyObject *value;     /* what was read, NULL for the default */
} Pending;

typedef struct {
    const char *p;
    const char *end;
    Pending *pending;
    Py_ssize_t pending_count;
    Py_ssize_t pending_capacity;
    int depth;
    int limit;  /* how deep a document goes, which is as far as Python recurses */
} Decoder;

static inline void
skip_whitespace(Decoder *d)
{
    while (d->p < d->end && (*d->p == ' ' || *d->p == '\t' || *d->p == '\n' || *d->p == '\r')) d->p++;
}

static int
add_pending(Decoder *d, PyObject *owner, Py_ssize_t index, FieldPlan *field, PyObject *value)
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
    item->field = field;
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

/* NULL without an error set is the decoder declining the input - NULL with one is a failure */
static PyObject *decode_any(Decoder *d);
static PyObject *decode_kind(Decoder *d, long kind, PyObject *arg, int *pending);

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

static int
hex4(const char *p, const char *end, Py_UCS4 *out)
{
    if (end - p < 4) return 0;
    Py_UCS4 value = 0;
    for (int i = 0; i < 4; i++) {
        char c = p[i];
        value <<= 4;
        if (c >= '0' && c <= '9') value |= (Py_UCS4)(c - '0');
        else if (c >= 'a' && c <= 'f') value |= (Py_UCS4)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') value |= (Py_UCS4)(c - 'A' + 10);
        else return 0;
    }
    *out = value;
    return 1;
}

/* a string with escapes - NULL without an error when one of them is not valid */
static PyObject *
unescape_string(const char *start, Py_ssize_t length)
{
    Py_UCS4 *buffer = PyMem_Malloc((size_t)(length + 1) * sizeof(Py_UCS4));
    if (buffer == NULL) return PyErr_NoMemory();
    Py_ssize_t count = 0;
    const char *p = start;
    const char *end = start + length;
    while (p < end) {
        unsigned char c = (unsigned char)*p;
        if (c != '\\') {
            if (c < 0x80) {
                buffer[count++] = c;
                p++;
            } else {  /* the text is valid UTF-8 - it came out of a str */
                int extra = c >= 0xf0 ? 3 : (c >= 0xe0 ? 2 : 1);
                Py_UCS4 point = c & (0x3f >> extra);
                p++;
                for (int i = 0; i < extra && p < end; i++, p++) point = (point << 6) | ((unsigned char)*p & 0x3f);
                buffer[count++] = point;
            }
            continue;
        }
        p++;  /* the escape, which is known to have a character after it */
        switch (*p) {
        case '"': buffer[count++] = '"'; p++; break;
        case '\\': buffer[count++] = '\\'; p++; break;
        case '/': buffer[count++] = '/'; p++; break;
        case 'b': buffer[count++] = '\b'; p++; break;
        case 'f': buffer[count++] = '\f'; p++; break;
        case 'n': buffer[count++] = '\n'; p++; break;
        case 'r': buffer[count++] = '\r'; p++; break;
        case 't': buffer[count++] = '\t'; p++; break;
        case 'u': {
            Py_UCS4 point;
            if (!hex4(p + 1, end, &point)) goto invalid;
            p += 5;
            if (point >= 0xd800 && point <= 0xdbff && end - p >= 6 && p[0] == '\\' && p[1] == 'u') {
                Py_UCS4 low;
                if (hex4(p + 2, end, &low) && low >= 0xdc00 && low <= 0xdfff) {
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
    if (!escaped) return PyUnicode_DecodeUTF8(start, length, NULL);
    return unescape_string(start, length);
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

/* a key of an object, matched with the names and aliases of the attributes - -1 for none */
static Py_ssize_t
field_for_key(CompiledPlan *plan, PyObject *key)
{
    PyObject *index = PyDict_GetItemWithError(plan->keys, key);
    if (index == NULL) {
        PyErr_Clear();
        return -1;
    }
    return PyLong_AsSsize_t(index);
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

static PyObject *
decode_struct_unbounded(Decoder *d, PyTypeObject *type)
{
    CompiledPlan *plan = compiled_plan_of(type);
    if (plan == NULL || type->tp_new != StructType.tp_new || type->tp_init != StructType.tp_init) return NULL;
    if (d->p >= d->end || *d->p != '{') return NULL;
    d->p++;

    StructObject *self = (StructObject *)type->tp_alloc(type, plan->count);
    if (self == NULL) return NULL;
    PyObject **items = ITEMS(self);
    char *given = PyMem_Calloc((size_t)plan->count + 1, 1);
    if (given == NULL) {
        Py_DECREF(self);
        return PyErr_NoMemory();
    }

    skip_whitespace(d);
    if (d->p < d->end && *d->p == '}') {
        d->p++;
    } else {
        for (;;) {
            skip_whitespace(d);
            PyObject *key = decode_string(d);
            if (key == NULL) goto fail;
            Py_ssize_t index = field_for_key(plan, key);
            Py_DECREF(key);
            if (index < 0 || given[index]) goto fail;  /* not an attribute, or given twice */
            given[index] = 1;
            skip_whitespace(d);
            if (d->p >= d->end || *d->p != ':') goto fail;
            d->p++;
            skip_whitespace(d);

            FieldPlan *field = &plan->fields[index];
            int pending = 0;
            PyObject *value = decode_kind(d, field->kind, field->arg, &pending);
            if (value == NULL) goto fail;
            if (pending) {
                if (!add_pending(d, (PyObject *)self, index, field, value)) {
                    Py_DECREF(value);
                    goto fail;
                }
            } else {
                items[index] = value;
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
        if (field->cached != NULL) items[i] = Py_NewRef(field->cached);
        else if (!add_pending(d, (PyObject *)self, i, field, NULL)) goto fail;  /* the default is called later */
    }
    PyMem_Free(given);
    return (PyObject *)self;

fail:
    PyMem_Free(given);
    Py_DECREF(self);
    return NULL;
}

/* a value read the way the annotation which validates it accepts it */
static PyObject *
decode_kind(Decoder *d, long kind, PyObject *arg, int *pending)
{
    switch (kind) {
    case K_INSTANCE:
        if (d->p < d->end && *d->p == '{') return decode_struct(d, (PyTypeObject *)arg);
        break;
    case K_OPTIONAL:
        if (d->p < d->end && *d->p == 'n') {
            PyObject *singletons = PyTuple_GET_ITEM(arg, 0);
            for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(singletons); i++) {
                if (PyTuple_GET_ITEM(singletons, i) == Py_None) {
                    return match_word(d, "null", 4) ? Py_NewRef(Py_None) : NULL;
                }
            }
        }
        return decode_kind(d, PyLong_AsLong(PyTuple_GET_ITEM(arg, 1)), PyTuple_GET_ITEM(arg, 2), pending);
    case K_SEQUENCE:
        if (d->p < d->end && *d->p == '[') {
            long element_kind = PyLong_AsLong(PyTuple_GET_ITEM(arg, 0));
            PyObject *element_arg = PyTuple_GET_ITEM(arg, 1);
            d->p++;
            PyObject *list = PyList_New(0);
            if (list == NULL) return NULL;
            skip_whitespace(d);
            if (d->p < d->end && *d->p == ']') {
                d->p++;
            } else {
                for (;;) {
                    skip_whitespace(d);
                    int element_pending = 0;
                    PyObject *element = decode_kind(d, element_kind, element_arg, &element_pending);
                    if (element == NULL || element_pending || PyList_Append(list, element) < 0) {
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
                        break;
                    }
                    Py_DECREF(list);
                    return NULL;
                }
            }
            PyObject *tuple = PyList_AsTuple(list);
            Py_DECREF(list);
            return tuple;
        }
        break;
    default:
        break;
    }

    /* a leaf, or a container which is not read in the shape of its annotation - read as it is */
    PyObject *raw = decode_any(d);
    if (raw == NULL) return NULL;
    if (kind == K_CALL) {
        *pending = 1;  /* an annotation of Python code - called once everything else is known to be valid */
        return raw;
    }
    PyObject *out = NULL;
    int r = fast_validate(kind, arg, raw, &out);
    Py_DECREF(raw);
    return r > 0 ? out : NULL;  /* undecided is for Python to decide */
}

/* the validators and the defaults of Python code, in the order the values were read */
static int
apply_pending(Decoder *d)
{
    for (Py_ssize_t i = 0; i < d->pending_count; i++) {
        Pending *item = &d->pending[i];
        PyObject *validated = item->value != NULL ? validate_field(item->field, item->value)
                                                  : PyObject_CallNoArgs(item->field->default_value);
        if (validated == NULL) {
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return JSON_ERROR;
            PyErr_Clear();  /* the Python implementation reports it with its position */
            return JSON_BAIL;
        }
        PyObject **slot = &ITEMS(item->owner)[item->index];
        Py_XSETREF(*slot, validated);
    }
    return JSON_OK;
}

/* the text of a json document as UTF-8 - NULL without an error when it is not one this reads */
static const char *
document_text(PyObject *value, Py_ssize_t *length, PyObject **owner)
{
    *owner = NULL;
    if (PyUnicode_Check(value)) return PyUnicode_AsUTF8AndSize(value, length);
    if (PyBytes_Check(value)) {
        /* what json.loads reads as UTF-8 - anything else is for it to detect */
        *owner = PyUnicode_DecodeUTF8(PyBytes_AS_STRING(value), PyBytes_GET_SIZE(value), "strict");
        if (*owner == NULL) return NULL;
        return PyUnicode_AsUTF8AndSize(*owner, length);
    }
    return NULL;
}

/* from_json(cls, text) - an instance, or None when the Python implementation has to read it */
static PyObject *
from_json_native(PyObject *mod, PyObject *args)
{
    PyObject *cls;
    PyObject *text;
    int array;
    if (!PyArg_ParseTuple(args, "OOp", &cls, &text, &array)) return NULL;
    if (!PyType_Check(cls) || !PyType_IsSubtype((PyTypeObject *)cls, &StructType)) {
        PyErr_SetString(PyExc_TypeError, "from_json expects a Struct class");
        return NULL;
    }

    Py_ssize_t length;
    PyObject *owner;
    const char *data = document_text(text, &length, &owner);
    if (data == NULL) {
        Py_XDECREF(owner);
        if (PyErr_Occurred()) {
            if (!PyErr_ExceptionMatches(PyExc_Exception)) return NULL;
            PyErr_Clear();  /* not valid text - json.loads says so */
        }
        Py_RETURN_NONE;
    }

    Decoder decoder = {data, data + length, NULL, 0, 0, 0, Py_GetRecursionLimit()};
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
    if (result != NULL) {
        int status = apply_pending(&decoder);
        if (status != JSON_OK) Py_CLEAR(result);
    }
    release_pending(&decoder);
    Py_XDECREF(owner);
    if (result == NULL && !PyErr_Occurred()) Py_RETURN_NONE;
    return result;
}
