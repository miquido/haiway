/* Whether a callable can be called the way an annotation says - a Callable, or a protocol
 * describing a function by its __call__. What is compared is the shape of the two signatures,
 * as the type checker compares it: the order, the names and the defaults of the parameters,
 * never their types. A function is read from its code, and so is a method bound to one, a
 * partial of one and an object called through one - every other callable has its signature
 * read by Python, and compared here the same way. */

/* the kinds of parameters, numbered as inspect.Parameter numbers them */
#define PARAMETER_POSITIONAL_ONLY 0
#define PARAMETER_POSITIONAL_OR_KEYWORD 1
#define PARAMETER_VAR_POSITIONAL 2
#define PARAMETER_KEYWORD_ONLY 3
#define PARAMETER_VAR_KEYWORD 4
#define PARAMETER_NONE -1 /* a parameter which takes nothing any more */

typedef struct {
    PyObject *name; /* borrowed - NULL for a positional only parameter of a Callable */
    int kind;
    int defaulted;
} Parameter;

typedef struct {
    Parameter *parameters;
    Py_ssize_t count;
    int extensible;        /* whether parameters not told may follow the ones told */
    PyObject *description; /* borrowed - how the function is called, wording the errors */
} Signature;

/* the parameters of a description - ((name, kind, defaulted), ...) - borrowing the names */
static Parameter *
signature_parameters(PyObject *described, Py_ssize_t *count)
{
    if (!PyTuple_Check(described)) {
        PyErr_SetString(PyExc_TypeError, "parameters are described by a tuple");
        return NULL;
    }
    Py_ssize_t n = PyTuple_GET_SIZE(described);
    Parameter *parameters = PyMem_RawCalloc((size_t)(n > 0 ? n : 1), sizeof(Parameter));
    if (parameters == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PyTuple_GET_ITEM(described, i);
        if (!PyTuple_Check(item) || PyTuple_GET_SIZE(item) != 3
            || !(PyUnicode_Check(PyTuple_GET_ITEM(item, 0)) || PyTuple_GET_ITEM(item, 0) == Py_None)
            || !PyLong_Check(PyTuple_GET_ITEM(item, 1)) || !PyBool_Check(PyTuple_GET_ITEM(item, 2))) {
            PyErr_SetString(PyExc_TypeError, "a parameter is described by (name, kind, defaulted)");
            PyMem_RawFree(parameters);
            return NULL;
        }
        long kind = PyLong_AsLong(PyTuple_GET_ITEM(item, 1));
        if (kind < PARAMETER_POSITIONAL_ONLY || kind > PARAMETER_VAR_KEYWORD) {
            if (!PyErr_Occurred()) PyErr_Format(PyExc_ValueError, "unknown parameter kind %ld", kind);
            PyMem_RawFree(parameters);
            return NULL;
        }
        PyObject *name = PyTuple_GET_ITEM(item, 0);
        parameters[i].name = name == Py_None ? NULL : name;
        parameters[i].kind = (int)kind;
        parameters[i].defaulted = PyTuple_GET_ITEM(item, 2) == Py_True;
    }
    *count = n;
    return parameters;
}

/* a signature of a description - (parameters, extensible, description) - which outlives it */
static Signature *
signature_compile(PyObject *described)
{
    if (!PyTuple_Check(described) || PyTuple_GET_SIZE(described) != 3
        || !PyBool_Check(PyTuple_GET_ITEM(described, 1)) || !PyUnicode_Check(PyTuple_GET_ITEM(described, 2))) {
        PyErr_SetString(PyExc_TypeError, "a function is described by (parameters, extensible, description)");
        return NULL;
    }
    Signature *signature = PyMem_RawCalloc(1, sizeof(Signature));
    if (signature == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    signature->parameters = signature_parameters(PyTuple_GET_ITEM(described, 0), &signature->count);
    if (signature->parameters == NULL) {
        PyMem_RawFree(signature);
        return NULL;
    }
    signature->extensible = PyTuple_GET_ITEM(described, 1) == Py_True;
    signature->description = PyTuple_GET_ITEM(described, 2);
    return signature;
}

static void
signature_free(Signature *signature)
{
    if (signature == NULL) return;
    PyMem_RawFree(signature->parameters);
    PyMem_RawFree(signature);
}

/* 1 when both are the same name, 0 when either has none or they differ, -1 on an error */
static int
same_name(PyObject *first, PyObject *second)
{
    if (first == NULL || second == NULL) return 0;
    if (first == second) return 1;
    return PyUnicode_Equal(first, second);
}

static int
has_parameter(const Parameter *parameters, Py_ssize_t count, int kind)
{
    for (Py_ssize_t i = 0; i < count; i++) {
        if (parameters[i].kind == kind) return 1;
    }
    return 0;
}

/* the parameters taking arguments by their position - those which come first */
static Py_ssize_t
positional_count(const Parameter *parameters, Py_ssize_t count)
{
    Py_ssize_t n = 0;
    while (n < count
        && (parameters[n].kind == PARAMETER_POSITIONAL_ONLY || parameters[n].kind == PARAMETER_POSITIONAL_OR_KEYWORD)) {
        n++;
    }
    return n;
}

/* whether a name given to parameters is taken - 1, 0, or -1 on an error. A positional parameter
 * of the name takes it unless it was given by position already, and the keywords of any name
 * take what no parameter does, a name of a positional only parameter included */
static int
name_taken(const Parameter *taking, Py_ssize_t taking_count, int any_keywords, PyObject *name, Py_ssize_t given)
{
    for (Py_ssize_t i = 0; i < taking_count; i++) {
        const Parameter *parameter = &taking[i];
        if (parameter->kind != PARAMETER_POSITIONAL_OR_KEYWORD && parameter->kind != PARAMETER_KEYWORD_ONLY) continue;
        int same = same_name(parameter->name, name);
        if (same < 0) return -1;
        if (!same) continue;
        return parameter->kind == PARAMETER_KEYWORD_ONLY || i >= given;
    }
    return any_keywords;
}

/* whether a told parameter given by name, past the first `positional` given by position, has
 * a name and no default - so every call gives it. 1, 0, or -1 on an error */
static int
always_named(const Parameter *told, Py_ssize_t told_count, Py_ssize_t told_positional, Py_ssize_t positional,
    PyObject *name)
{
    for (Py_ssize_t i = positional; i < told_count; i++) {
        const Parameter *expected = &told[i];
        int named = i < told_positional ? expected->kind == PARAMETER_POSITIONAL_OR_KEYWORD
                                        : expected->kind == PARAMETER_KEYWORD_ONLY;
        if (!named || expected->defaulted) continue;
        int same = same_name(expected->name, name);
        if (same != 0) return same;
    }
    return 0;
}

/* whether parameters take the calls giving the first `positional` told parameters by position,
 * `given` arguments by position in all, and the rest by name or not at all - 1, 0, or -1. A
 * name only fails on its own, and a parameter only by being given nothing, so the calls which
 * differ in what they name are told by the names which may be given and those always given */
static int
calls_taken(const Parameter *taking, Py_ssize_t taking_count, const Parameter *told, Py_ssize_t told_count,
    Py_ssize_t positional, Py_ssize_t given)
{
    Py_ssize_t taking_positional = positional_count(taking, taking_count);
    Py_ssize_t told_positional = positional_count(told, told_count);
    int any_keywords = has_parameter(taking, taking_count, PARAMETER_VAR_KEYWORD);
    if (given > taking_positional && !has_parameter(taking, taking_count, PARAMETER_VAR_POSITIONAL)) return 0;

    for (Py_ssize_t i = positional; i < told_count; i++) {
        const Parameter *expected = &told[i];
        int named = i < told_positional ? expected->kind == PARAMETER_POSITIONAL_OR_KEYWORD
                                        : expected->kind == PARAMETER_KEYWORD_ONLY;
        if (!named) continue;
        int r = name_taken(taking, taking_count, any_keywords, expected->name, given);
        if (r <= 0) return r;
    }
    for (Py_ssize_t i = 0; i < taking_count; i++) {
        const Parameter *parameter = &taking[i];
        if (parameter->defaulted) continue;
        if (i < taking_positional) {
            if (i < given) continue; /* given by position */
            if (parameter->kind == PARAMETER_POSITIONAL_ONLY) return 0;
        } else if (parameter->kind != PARAMETER_KEYWORD_ONLY) {
            continue; /* the variadic ones take what there is */
        }
        int r = always_named(told, told_count, told_positional, positional, parameter->name);
        if (r <= 0) return r;
    }
    return 1;
}

/* whether parameters take every call the told ones are made with - 1, 0, or -1 on an error.
 * The told positional parameters are given by position up to some of them, the rest by name
 * or left out for their defaults, and the variadic ones some more - each such number of them
 * is tried. With more told to follow, the told positional ones only have to fit */
static int
signature_fits(const Parameter *taking, Py_ssize_t taking_count, const Parameter *told, Py_ssize_t told_count,
    int extensible)
{
    Py_ssize_t taking_positional = positional_count(taking, taking_count);
    Py_ssize_t told_positional = positional_count(told, told_count);
    int taking_variadic = has_parameter(taking, taking_count, PARAMETER_VAR_POSITIONAL);
    if (extensible) return told_positional <= taking_positional || taking_variadic;
    if (has_parameter(told, told_count, PARAMETER_VAR_KEYWORD)
        && !has_parameter(taking, taking_count, PARAMETER_VAR_KEYWORD)) {
        return 0; /* given a name nothing else takes */
    }
    /* the fewest given by position - all after it are given by name, or left out */
    Py_ssize_t fewest = told_positional;
    while (fewest > 0
        && (told[fewest - 1].kind == PARAMETER_POSITIONAL_OR_KEYWORD || told[fewest - 1].defaulted)) {
        fewest--;
    }
    int told_variadic = has_parameter(told, told_count, PARAMETER_VAR_POSITIONAL);
    for (Py_ssize_t positional = fewest; positional <= told_positional; positional++) {
        int r = calls_taken(taking, taking_count, told, told_count, positional, positional);
        if (r <= 0) return r;
    }
    if (told_variadic) { /* given more than any parameter takes by position */
        Py_ssize_t more = (taking_positional > told_positional ? taking_positional : told_positional) + 1;
        int r = calls_taken(taking, taking_count, told, told_count, told_positional, more);
        if (r <= 0) return r;
    }
    return 1;
}

/* the length of what a function attribute holds, 0 for None - -1 on an error */
static Py_ssize_t
function_values(PyObject *function, PyObject *name)
{
    PyObject *values = PyObject_GetAttr(function, name);
    if (values == NULL) return -1;
    Py_ssize_t count = values == Py_None ? 0 : PyObject_Length(values);
    Py_DECREF(values);
    return count;
}

/* the parameters a function takes, read from its code once it was given its first `bound`
 * arguments and the `keywords` of a partial (a dict or NULL) - a parameter given by name has
 * a default, and every positional one after it can be given by name only. The parameters,
 * which borrow from `*names` (a new reference), their count in `*count` - NULL with no error
 * for a function which can't be called that way at all */
static Parameter *
function_parameters(ModuleState *state, PyObject *function, Py_ssize_t bound, PyObject *keywords,
    Py_ssize_t *count, PyObject **names)
{
    *names = NULL;
    /* read through the attributes - each may be replaced while it is read */
    PyObject *code = PyObject_GetAttr(function, state->str_code);
    if (code == NULL) return NULL;
    if (!PyCode_Check(code)) {
        Py_DECREF(code);
        PyErr_SetString(PyExc_TypeError, "the code of a function is not a code object");
        return NULL;
    }
    PyCodeObject *co = (PyCodeObject *)code;
    Py_ssize_t positional = co->co_argcount;
    Py_ssize_t posonly = co->co_posonlyargcount;
    Py_ssize_t keyword = co->co_kwonlyargcount;
    int variadic = (co->co_flags & CO_VARARGS) != 0;
    int any_keywords = (co->co_flags & CO_VARKEYWORDS) != 0;
    *names = PyCode_GetVarnames(co);
    Py_DECREF(code);
    if (*names == NULL) return NULL;
    if (PyTuple_GET_SIZE(*names) < positional + keyword) {
        PyErr_SetString(PyExc_TypeError, "the code of a function names fewer arguments than it takes");
        goto failed;
    }
    if (bound > positional && !variadic) goto uncallable; /* given more than it takes */

    Py_ssize_t defaults = function_values(function, state->str_defaults);
    if (defaults < 0) goto failed;
    PyObject *keyword_defaults = NULL;
    if (keyword > 0) {
        keyword_defaults = PyObject_GetAttr(function, state->str_kwdefaults);
        if (keyword_defaults == NULL) goto failed;
    }
    Parameter *parameters = PyMem_Calloc((size_t)(positional + keyword + 2), sizeof(Parameter));
    if (parameters == NULL) {
        Py_XDECREF(keyword_defaults);
        PyErr_NoMemory();
        goto failed;
    }
    Py_ssize_t n = 0;
    for (Py_ssize_t i = bound; i < positional; i++, n++) {
        parameters[n].name = PyTuple_GET_ITEM(*names, i);
        parameters[n].kind = i < posonly ? PARAMETER_POSITIONAL_ONLY : PARAMETER_POSITIONAL_OR_KEYWORD;
        parameters[n].defaulted = i >= positional - defaults;
    }
    if (variadic) parameters[n++].kind = PARAMETER_VAR_POSITIONAL;
    for (Py_ssize_t i = positional; i < positional + keyword; i++, n++) {
        parameters[n].name = PyTuple_GET_ITEM(*names, i);
        parameters[n].kind = PARAMETER_KEYWORD_ONLY;
        int defaulted = keyword_defaults != NULL && PyDict_Check(keyword_defaults)
            ? PyDict_Contains(keyword_defaults, parameters[n].name) : 0;
        if (defaulted < 0) {
            Py_XDECREF(keyword_defaults);
            PyMem_Free(parameters);
            goto failed;
        }
        parameters[n].defaulted = defaulted;
    }
    Py_XDECREF(keyword_defaults);
    if (any_keywords) parameters[n++].kind = PARAMETER_VAR_KEYWORD;
    *count = n;
    if (keywords == NULL || PyDict_GET_SIZE(keywords) == 0) return parameters;

    /* what a partial gives by name */
    Py_ssize_t matched = 0;
    for (Py_ssize_t i = 0; i < positional + keyword; i++) {
        int given = PyDict_Contains(keywords, PyTuple_GET_ITEM(*names, i));
        if (given < 0) {
            PyMem_Free(parameters);
            goto failed;
        }
        if (!given) continue;
        if (i < posonly) {
            if (!any_keywords) { /* a positional only parameter is never given by name */
                PyMem_Free(parameters);
                goto uncallable;
            }
            matched += 1; /* taken by the keywords of any name, leaving the parameter be */
            continue;
        }
        if (i < bound && i < positional) { /* given by position already */
            PyMem_Free(parameters);
            goto uncallable;
        }
        matched += 1;
    }
    if (matched < PyDict_GET_SIZE(keywords) && !any_keywords) {
        PyMem_Free(parameters);
        goto uncallable;
    }
    int named = 0; /* whether a positional parameter was given by name - the ones after it too */
    for (Py_ssize_t i = 0; i < n; i++) {
        Parameter *parameter = &parameters[i];
        if (parameter->kind == PARAMETER_VAR_POSITIONAL && named) {
            parameter->kind = PARAMETER_NONE; /* given anything, it gives the named one twice */
            continue;
        }
        if (parameter->name == NULL || parameter->kind == PARAMETER_POSITIONAL_ONLY) continue;
        int given = PyDict_Contains(keywords, parameter->name);
        if (given < 0) {
            PyMem_Free(parameters);
            goto failed;
        }
        if (given) {
            named = named || parameter->kind == PARAMETER_POSITIONAL_OR_KEYWORD;
            parameter->kind = PARAMETER_KEYWORD_ONLY;
            parameter->defaulted = 1;
        } else if (named && parameter->kind == PARAMETER_POSITIONAL_OR_KEYWORD) {
            parameter->kind = PARAMETER_KEYWORD_ONLY;
        }
    }
    return parameters;

uncallable: /* no error set - it can't be called the way it is given at all */
failed:
    Py_CLEAR(*names);
    return NULL;
}

/* a partial is the function it calls, given its arguments first and its keywords by name - a
 * new reference to what is called in the end and to the keywords, NULL for no keywords. NULL
 * on an error, and on a partial of a partial giving keywords both, which is left to Python */
static PyObject *
unwrap_partial(ModuleState *state, PyObject *value, Py_ssize_t *bound, PyObject **keywords)
{
    PyObject *callee = Py_NewRef(value);
    *keywords = NULL;
    while (Py_IS_TYPE(callee, (PyTypeObject *)state->partial)) {
        PyObject *named = PyObject_GetAttr(callee, state->str_keywords);
        if (named == NULL) goto failed;
        if (!PyDict_Check(named) || PyDict_GET_SIZE(named) == 0) {
            Py_DECREF(named);
        } else if (*keywords == NULL) {
            *keywords = named;
        } else {
            Py_DECREF(named);
            goto failed; /* merged by functools as it creates them - rare enough for Python */
        }
        Py_ssize_t arguments = function_values(callee, state->str_args);
        if (arguments < 0) goto failed;
        PyObject *function = PyObject_GetAttr(callee, state->str_func);
        Py_SETREF(callee, function);
        if (callee == NULL) goto failed;
        *bound += arguments;
    }
    return callee;

failed:
    Py_XDECREF(callee);
    Py_CLEAR(*keywords);
    return NULL;
}

/* the function an object is called through - the __call__ its class defines, found as the call
 * itself finds it, along the classes it is made of rather than on the object. A new reference
 * to that function, given what it binds first, or NULL when it is not a function written in
 * Python - 0, or -1 on an error */
static int
class_call(ModuleState *state, PyTypeObject *type, PyObject **function, Py_ssize_t *bound)
{
    *function = NULL;
    PyObject *mro = PyObject_GetAttrString((PyObject *)type, "__mro__");
    if (mro == NULL) return -1;
    if (!PyTuple_Check(mro)) {
        Py_DECREF(mro);
        return 0;
    }
    PyObject *found = NULL;
    for (Py_ssize_t i = 0; found == NULL && i < PyTuple_GET_SIZE(mro); i++) {
        PyObject *base = PyTuple_GET_ITEM(mro, i);
        if (!PyType_Check(base)) continue;
        PyObject *namespace = PyType_GetDict((PyTypeObject *)base);
        if (namespace == NULL) {
            Py_DECREF(mro);
            return -1;
        }
        int r = PyDict_GetItemRef(namespace, state->str_call, &found);
        Py_DECREF(namespace);
        if (r < 0) {
            Py_DECREF(mro);
            return -1;
        }
    }
    Py_DECREF(mro);
    if (found == NULL) return 0;
    if (PyFunction_Check(found)) {
        *function = found;
        *bound = 1; /* the object itself comes first */
        return 0;
    }
    if (Py_IS_TYPE(found, &PyStaticMethod_Type)) {
        PyObject *wrapped = PyObject_GetAttrString(found, "__func__");
        Py_DECREF(found);
        if (wrapped == NULL) return -1;
        if (PyFunction_Check(wrapped)) *function = wrapped; /* given nothing first */
        else Py_DECREF(wrapped);
        return 0;
    }
    Py_DECREF(found); /* a descriptor of another kind - asked of Python */
    return 0;
}

/* whether a callable can be called the way a signature tells - 1, 0, or -1 on an error */
static int
signature_accepts(ModuleState *state, Signature *signature, PyObject *described, PyObject *value)
{
    Py_ssize_t bound = 0;
    PyObject *keywords = NULL;
    PyObject *callee = unwrap_partial(state, value, &bound, &keywords);
    PyObject *function = NULL;
    if (callee == NULL) {
        if (PyErr_Occurred()) return -1;
    } else if (PyFunction_Check(callee)) {
        function = Py_NewRef(callee);
    } else if (Py_IS_TYPE(callee, &PyMethod_Type)) {
        function = Py_NewRef(PyMethod_GET_FUNCTION(callee)); /* a method never changes what it binds */
        bound += 1;                                           /* what it is bound to comes first */
    } else if (!PyType_Check(callee)) {
        Py_ssize_t first = 0;
        if (class_call(state, Py_TYPE(callee), &function, &first) < 0) {
            Py_DECREF(callee);
            Py_XDECREF(keywords);
            return -1;
        }
        bound += first;
    }
    Py_XDECREF(callee);
    int r;
    if (function != NULL && PyFunction_Check(function)) {
        Py_ssize_t count = 0;
        PyObject *names = NULL;
        Parameter *parameters = function_parameters(state, function, bound, keywords, &count, &names);
        if (parameters == NULL) {
            r = PyErr_Occurred() ? -1 : 0;
        } else {
            r = signature_fits(parameters, count, signature->parameters, signature->count, signature->extensible);
            PyMem_Free(parameters);
        }
        Py_XDECREF(names);
    } else { /* asked of Python as it was given - its signature reads partials on its own */
        PyObject *accepted = PyObject_CallFunctionObjArgs(state->accepts, value, described, NULL);
        r = accepted == NULL ? -1 : PyObject_IsTrue(accepted);
        Py_XDECREF(accepted);
    }
    Py_XDECREF(function);
    Py_XDECREF(keywords);
    return r;
}

/* signature_fits(parameters, told, extensible) - the parameters Python read of a callable,
 * compared the way the parameters of a function are */
static PyObject *
signature_fits_native(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "signature_fits(parameters, told, extensible)");
        return NULL;
    }
    Py_ssize_t taking_count = 0, told_count = 0;
    Parameter *taking = signature_parameters(args[0], &taking_count);
    if (taking == NULL) return NULL;
    Parameter *told = signature_parameters(args[1], &told_count);
    if (told == NULL) {
        PyMem_RawFree(taking);
        return NULL;
    }
    int r = signature_fits(taking, taking_count, told, told_count, PyObject_IsTrue(args[2]) > 0);
    PyMem_RawFree(taking);
    PyMem_RawFree(told);
    if (r < 0) return NULL;
    return PyBool_FromLong(r);
}
