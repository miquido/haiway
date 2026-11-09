/* ------------------------------------------------------------------- json */

/*
 * The JSON of an instance written without the intermediate basic object. The writer is
 * not the authority on what has no spelling: a value it does not know is given to the
 * basic conversion, and what that leaves as it is goes back to the standard library,
 * which is the one raising the error.
 */

typedef struct {
    ModuleState *state;
    char *data;
    Py_ssize_t length;
    Py_ssize_t capacity;
} Buffer;

enum { JSON_ERROR = -1, JSON_BAIL = 0, JSON_OK = 1 };

static int
buffer_reserve(Buffer *buffer, Py_ssize_t extra)
{
    if (buffer->length + extra <= buffer->capacity) return 1;
    Py_ssize_t capacity = buffer->capacity == 0 ? 256 : buffer->capacity;
    while (capacity < buffer->length + extra) capacity *= 2;
    char *data = PyMem_Realloc(buffer->data, (size_t)capacity);
    if (data == NULL) {
        PyErr_NoMemory();
        return 0;
    }
    buffer->data = data;
    buffer->capacity = capacity;
    return 1;
}

static inline int
buffer_put(Buffer *buffer, const char *text, Py_ssize_t length)
{
    if (!buffer_reserve(buffer, length)) return 0;
    memcpy(buffer->data + buffer->length, text, (size_t)length);
    buffer->length += length;
    return 1;
}

static inline int
buffer_putc(Buffer *buffer, char c)
{
    if (!buffer_reserve(buffer, 1)) return 0;
    buffer->data[buffer->length++] = c;
    return 1;
}

static inline int
needs_escape(Py_UCS4 c)
{
    return c < ' ' || c > '~' || c == '"' || c == '\\';
}

static inline char *
put_escaped(char *out, Py_UCS4 c)
{
    static const char hex[] = "0123456789abcdef";
    *out++ = '\\';
    switch (c) {
    case '"': *out++ = '"'; break;
    case '\\': *out++ = '\\'; break;
    case '\n': *out++ = 'n'; break;
    case '\r': *out++ = 'r'; break;
    case '\t': *out++ = 't'; break;
    case '\b': *out++ = 'b'; break;
    case '\f': *out++ = 'f'; break;
    default:
        if (c >= 0x10000) {  /* a surrogate pair */
            Py_UCS4 v = c - 0x10000;
            Py_UCS4 high = 0xd800 | ((v >> 10) & 0x3ff);
            Py_UCS4 low = 0xdc00 | (v & 0x3ff);
            *out++ = 'u';
            *out++ = hex[(high >> 12) & 0xf];
            *out++ = hex[(high >> 8) & 0xf];
            *out++ = hex[(high >> 4) & 0xf];
            *out++ = hex[high & 0xf];
            *out++ = '\\';
            *out++ = 'u';
            *out++ = hex[(low >> 12) & 0xf];
            *out++ = hex[(low >> 8) & 0xf];
            *out++ = hex[(low >> 4) & 0xf];
            *out++ = hex[low & 0xf];
        } else {
            *out++ = 'u';
            *out++ = hex[(c >> 12) & 0xf];
            *out++ = hex[(c >> 8) & 0xf];
            *out++ = hex[(c >> 4) & 0xf];
            *out++ = hex[c & 0xf];
        }
    }
    return out;
}

/*
 * A string as json spells it, ASCII only - `json.dumps` does the same by default. An
 * ASCII string with nothing to escape, which is most of them, is copied as it is; the
 * rest is written in bounded pieces, so no more room is ever taken than a piece needs.
 */
#define STRING_PIECE 512

static int
write_string(Buffer *buffer, PyObject *text)
{
    Py_ssize_t length = PyUnicode_GET_LENGTH(text);
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    if (PyUnicode_IS_ASCII(text)) {
        const unsigned char *bytes = data;
        Py_ssize_t i = 0;
        while (i < length && !needs_escape(bytes[i])) i++;
        if (i == length) {
            if (!buffer_reserve(buffer, length + 2)) return 0;
            char *out = buffer->data + buffer->length;
            *out++ = '"';
            memcpy(out, bytes, (size_t)length);
            out += length;
            *out++ = '"';
            buffer->length = out - buffer->data;
            return 1;
        }
    }
    if (!buffer_putc(buffer, '"')) return 0;
    Py_ssize_t per_char = kind == PyUnicode_4BYTE_KIND ? 12 : 6;  /* the worst case of a character */
    for (Py_ssize_t start = 0; start < length; start += STRING_PIECE) {
        Py_ssize_t stop = start + STRING_PIECE < length ? start + STRING_PIECE : length;
        if (!buffer_reserve(buffer, (stop - start) * per_char)) return 0;
        char *out = buffer->data + buffer->length;
        for (Py_ssize_t i = start; i < stop; i++) {
            Py_UCS4 c = PyUnicode_READ(kind, data, i);
            if (needs_escape(c)) out = put_escaped(out, c);
            else *out++ = (char)c;
        }
        buffer->length = out - buffer->data;
    }
    return buffer_putc(buffer, '"');
}

/* the key of an attribute as json spells it, followed by the colon - what a plan keeps */
static PyObject *
json_key_bytes(PyObject *key)
{
    Buffer buffer = {NULL, NULL, 0, 0};
    PyObject *result = NULL;
    if (write_string(&buffer, key) && buffer_put(&buffer, ": ", 2)) {
        result = PyBytes_FromStringAndSize(buffer.data, buffer.length);
    }
    PyMem_Free(buffer.data);
    return result;
}

static int write_json(Buffer *buffer, PyObject *value);

static int
write_text_of(Buffer *buffer, PyObject *text)
{
    if (text == NULL) return JSON_ERROR;
    Py_ssize_t length;
    const char *ascii = PyUnicode_AsUTF8AndSize(text, &length);
    int ok = ascii != NULL && buffer_put(buffer, ascii, length);
    Py_DECREF(text);
    return ok ? JSON_OK : JSON_ERROR;
}

static int
write_struct_json(Buffer *buffer, PyObject *self)
{
    CompiledPlan *plan = PLAN_OF(Py_TYPE(self));
    if (plan == NULL) return JSON_BAIL;
    if (!buffer_putc(buffer, '{')) return JSON_ERROR;
    int first = 1;
    for (Py_ssize_t i = 0; i < plan->count; i++) {
        PyObject *value = ITEMS(self)[i];
        if (value == NULL || value == buffer->state->missing) continue;
        FieldPlan *field = &plan->fields[i];
        if (!first && !buffer_put(buffer, ", ", 2)) return JSON_ERROR;
        first = 0;
        if (!buffer_put(buffer, field->json_key, field->json_key_length)) return JSON_ERROR;
        int r = write_json(buffer, value);
        if (r != JSON_OK) return r;
    }
    return buffer_putc(buffer, '}') ? JSON_OK : JSON_ERROR;
}

static int
write_json_unbounded(Buffer *buffer, PyObject *value)
{
    ModuleState *state = buffer->state;
    if (value == Py_None) return buffer_put(buffer, "null", 4) ? JSON_OK : JSON_ERROR;
    if (value == Py_True) return buffer_put(buffer, "true", 4) ? JSON_OK : JSON_ERROR;
    if (value == Py_False) return buffer_put(buffer, "false", 5) ? JSON_OK : JSON_ERROR;
    if (PyLong_Check(value)) {
        /* an enum of ints is written as the int it is */
        int overflow = 0;
        long long number = PyLong_AsLongLongAndOverflow(value, &overflow);
        if (!overflow && !(number == -1 && PyErr_Occurred())) {
            char digits[24];
            char *end = digits + sizeof(digits);
            char *at = end;
            unsigned long long magnitude = number < 0 ? 0ULL - (unsigned long long)number : (unsigned long long)number;
            do {
                *--at = (char)('0' + magnitude % 10);
                magnitude /= 10;
            } while (magnitude != 0);
            if (number < 0) *--at = '-';
            return buffer_put(buffer, at, end - at) ? JSON_OK : JSON_ERROR;
        }
        PyErr_Clear();
        return write_text_of(buffer, PyLong_Type.tp_repr(value));
    }
    if (PyFloat_Check(value)) {
        double number = PyFloat_AS_DOUBLE(value);
        if (isnan(number)) return buffer_put(buffer, "NaN", 3) ? JSON_OK : JSON_ERROR;
        if (isinf(number)) {
            return (number > 0 ? buffer_put(buffer, "Infinity", 8) : buffer_put(buffer, "-Infinity", 9)) ? JSON_OK : JSON_ERROR;
        }
        /* the shortest spelling which reads back the same - what `repr` of a float is */
        char *text = PyOS_double_to_string(number, 'r', 0, Py_DTSF_ADD_DOT_0, NULL);
        if (text == NULL) return JSON_ERROR;
        int ok = buffer_put(buffer, text, (Py_ssize_t)strlen(text));
        PyMem_Free(text);
        return ok ? JSON_OK : JSON_ERROR;
    }
    if (PyUnicode_Check(value)) return write_string(buffer, value) ? JSON_OK : JSON_ERROR;
    if (is_struct(state, value)) return write_struct_json(buffer, value);

    if (PyTuple_CheckExact(value) || PyList_CheckExact(value)) {
        PyObject *items = PySequence_Tuple(value);  /* a snapshot of a list, the tuple itself otherwise */
        if (items == NULL) return JSON_ERROR;
        Py_ssize_t n = PyTuple_GET_SIZE(items);
        int status = buffer_putc(buffer, '[') ? JSON_OK : JSON_ERROR;
        for (Py_ssize_t i = 0; status == JSON_OK && i < n; i++) {
            if (i > 0 && !buffer_put(buffer, ", ", 2)) status = JSON_ERROR;
            else status = write_json(buffer, PyTuple_GET_ITEM(items, i));
        }
        Py_DECREF(items);
        if (status == JSON_OK && !buffer_putc(buffer, ']')) status = JSON_ERROR;
        return status;
    }

    PyObject *source = dict_source(value);
    if (source == NULL && PyErr_Occurred()) return JSON_ERROR;
    if (source != NULL) {
        int status = JSON_OK;
        if (!buffer_putc(buffer, '{')) status = JSON_ERROR;
        PyObject *key;
        PyObject *item;
        Py_ssize_t position = 0;
        int first = 1;
        while (status == JSON_OK && PyDict_Next(source, &position, &key, &item)) {
            if (!PyUnicode_Check(key)) {
                status = JSON_BAIL;  /* spelled as a key by the standard library */
                break;
            }
            if (!first && !buffer_put(buffer, ", ", 2)) status = JSON_ERROR;
            else if (!write_string(buffer, key) || !buffer_put(buffer, ": ", 2)) status = JSON_ERROR;
            else status = write_json(buffer, item);
            first = 0;
        }
        Py_DECREF(source);
        if (status == JSON_OK && !buffer_putc(buffer, '}')) status = JSON_ERROR;
        return status;
    }

    PyObject *spelled = native_spelling(state, value);
    if (spelled != NULL) {
        int ok = write_string(buffer, spelled);
        Py_DECREF(spelled);
        return ok ? JSON_OK : JSON_ERROR;
    }
    if (PyErr_Occurred()) return JSON_ERROR;

    /* anything else has the spelling the basic conversion gives it, if it has one */
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) return JSON_ERROR;
    PyObject *basic = PyObject_CallFunction(state->basic_value, "OOO", value, Py_False, empty);
    Py_DECREF(empty);
    if (basic == NULL) return JSON_ERROR;
    if (basic == value) {  /* left to the encoder of the caller */
        Py_DECREF(basic);
        return JSON_BAIL;
    }
    int r = write_json(buffer, basic);
    Py_DECREF(basic);
    return r;
}

static int
write_json(Buffer *buffer, PyObject *value)
{
    if (Py_EnterRecursiveCall(" while writing json")) return JSON_ERROR;
    int result = write_json_unbounded(buffer, value);
    Py_LeaveRecursiveCall();
    return result;
}

/* to_json(instance) - the json of an instance as `json.dumps` writes it, None when not handled here */
static PyObject *
to_json_native(PyObject *mod, PyObject *self)
{
    ModuleState *state = module_state(mod);
    if (!is_struct(state, self)) {
        PyErr_SetString(PyExc_TypeError, "to_json expects a State");
        return NULL;
    }
    Buffer buffer = {state, NULL, 0, 0};
    int status = write_json(&buffer, self);
    PyObject *result = NULL;
    if (status == JSON_OK) {
        result = PyUnicode_DecodeASCII(buffer.data, buffer.length, NULL);
    } else if (status == JSON_BAIL) {
        result = Py_NewRef(Py_None);
    }
    PyMem_Free(buffer.data);
    return result;
}
