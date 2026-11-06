/* ------------------------------------------------------------------- json */

/*
 * The JSON of an instance written without the intermediate basic object, and an
 * instance read from JSON without the intermediate Python containers. Neither of them
 * is the authority on what is wrong with an input - anything which is not
 * the usual case (a type with no spelling, a key which is not an attribute, a syntax
 * error, a value which needs a conversion the native code does not know) is handed
 * back to the Python implementation, which is the one raising the errors.
 */

typedef struct {
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

static int
buffer_put(Buffer *buffer, const char *text, Py_ssize_t length)
{
    if (!buffer_reserve(buffer, length)) return 0;
    memcpy(buffer->data + buffer->length, text, (size_t)length);
    buffer->length += length;
    return 1;
}

static int
buffer_putc(Buffer *buffer, char c)
{
    if (!buffer_reserve(buffer, 1)) return 0;
    buffer->data[buffer->length++] = c;
    return 1;
}

/* a string as json spells it, ASCII only - `json.dumps` does the same by default */
static int
write_string(Buffer *buffer, PyObject *text)
{
    static const char hex[] = "0123456789abcdef";
    Py_ssize_t length = PyUnicode_GET_LENGTH(text);
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    if (!buffer_reserve(buffer, length * 12 + 2)) return 0;  /* the worst case, a surrogate pair each */
    char *out = buffer->data + buffer->length;
    *out++ = '"';
    for (Py_ssize_t i = 0; i < length; i++) {
        Py_UCS4 c = PyUnicode_READ(kind, data, i);
        if (c >= ' ' && c <= '~' && c != '"' && c != '\\') {
            *out++ = (char)c;
            continue;
        }
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
    }
    *out++ = '"';
    buffer->length = out - buffer->data;
    return 1;
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
    CompiledPlan *plan = compiled_plan_of(Py_TYPE(self));
    Py_ssize_t count = plan == NULL ? 0 : plan->count;
    if (plan == NULL && Py_SIZE(self) != 0) return JSON_BAIL;
    if (count > Py_SIZE(self)) return JSON_BAIL;
    if (!buffer_putc(buffer, '{')) return JSON_ERROR;
    int first = 1;
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *value = ITEMS(self)[i];
        if (value == NULL || value == hook_missing) continue;
        FieldPlan *field = &plan->fields[i];
        if (!first && !buffer_put(buffer, ", ", 2)) return JSON_ERROR;
        first = 0;
        if (!write_string(buffer, field_key(field))) return JSON_ERROR;
        if (!buffer_put(buffer, ": ", 2)) return JSON_ERROR;
        int r = write_json(buffer, value);
        if (r != JSON_OK) return r;
    }
    return buffer_putc(buffer, '}') ? JSON_OK : JSON_ERROR;
}

static int
write_json_unbounded(Buffer *buffer, PyObject *value)
{
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
        if (Py_IS_NAN(number)) return buffer_put(buffer, "NaN", 3) ? JSON_OK : JSON_ERROR;
        if (Py_IS_INFINITY(number)) {
            return (number > 0 ? buffer_put(buffer, "Infinity", 8) : buffer_put(buffer, "-Infinity", 9)) ? JSON_OK : JSON_ERROR;
        }
        return write_text_of(buffer, PyFloat_Type.tp_repr(value));
    }
    if (PyUnicode_Check(value)) return write_string(buffer, value) ? JSON_OK : JSON_ERROR;
    if (is_struct(value)) return write_struct_json(buffer, value);

    if (PyTuple_CheckExact(value) || PyList_CheckExact(value)) {
        Py_ssize_t n = PySequence_Fast_GET_SIZE(value);
        if (!buffer_putc(buffer, '[')) return JSON_ERROR;
        for (Py_ssize_t i = 0; i < n; i++) {
            if (i > 0 && !buffer_put(buffer, ", ", 2)) return JSON_ERROR;
            int r = write_json(buffer, PySequence_Fast_GET_ITEM(value, i));
            if (r != JSON_OK) return r;
        }
        return buffer_putc(buffer, ']') ? JSON_OK : JSON_ERROR;
    }

    int error = 0;
    PyObject *items = mapping_items(value, &error);
    if (error) return JSON_ERROR;
    if (items != NULL) {
        int status = JSON_OK;
        if (!buffer_putc(buffer, '{')) status = JSON_ERROR;
        for (Py_ssize_t i = 0; status == JSON_OK && i < PyList_GET_SIZE(items); i++) {
            PyObject *pair = PyList_GET_ITEM(items, i);
            PyObject *key = PyTuple_GET_ITEM(pair, 0);
            if (!PyUnicode_Check(key)) {
                status = JSON_BAIL;  /* spelled as a key by the Python implementation */
                break;
            }
            if (i > 0 && !buffer_put(buffer, ", ", 2)) status = JSON_ERROR;
            else if (!write_string(buffer, key) || !buffer_put(buffer, ": ", 2)) status = JSON_ERROR;
            else status = write_json(buffer, PyTuple_GET_ITEM(pair, 1));
        }
        Py_DECREF(items);
        if (status == JSON_OK && !buffer_putc(buffer, '}')) status = JSON_ERROR;
        return status;
    }

    PyObject *spelled = native_spelling(value);
    if (spelled != NULL) {
        int ok = write_string(buffer, spelled);
        Py_DECREF(spelled);
        return ok ? JSON_OK : JSON_ERROR;
    }
    if (PyErr_Occurred()) return JSON_ERROR;

    /* anything else has the spelling the basic conversion gives it, if it has one */
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) return JSON_ERROR;
    PyObject *basic = PyObject_CallFunction(hook_basic_value, "OOO", value, Py_False, empty);
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
    if (!is_struct(self)) {
        PyErr_SetString(PyExc_TypeError, "to_json expects a State");
        return NULL;
    }
    Buffer buffer = {NULL, 0, 0};
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
