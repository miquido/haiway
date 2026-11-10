/* Whether a callable accepts the calls an annotation describes - a Callable, or a protocol
 * declaring only __call__. Only the shape of the signatures is compared, as a type checker
 * compares it: the order, kinds, names and defaults of the parameters, never their types.
 * Python functions are read from their code - also when called through a bound method, a
 * partial or the __call__ of an object. Any other callable has its signature read by inspect
 * (the `parameters` hook) and is compared the same way. */

/* the kinds of parameters, numbered as inspect.Parameter numbers them, and two more used
 * while applying what a partial or a bound method already passes */
typedef enum {
    PARAMETER_GIVEN = -2, /* already passed by position - passing its name again fails the call */
    PARAMETER_NONE = -1,  /* accepts no arguments any more */
    PARAMETER_POSITIONAL_ONLY = 0,
    PARAMETER_POSITIONAL_OR_KEYWORD = 1,
    PARAMETER_VAR_POSITIONAL = 2,
    PARAMETER_KEYWORD_ONLY = 3,
    PARAMETER_VAR_KEYWORD = 4,
} ParameterKind;

typedef struct {
    PyObject *name; /* borrowed - NULL for a positional only parameter */
    ParameterKind kind;
    int defaulted;
} Parameter;

/* parameters read into a stack buffer of this size before allocating - validation never
 * recurses through a signature check, so the buffer does not grow deep validation frames */
#define SIGNATURE_LOCAL 12
/* the most nested partials and bound methods unwrapped - only a cycle reaches it */
#define SIGNATURE_DEPTH 1024

typedef struct {
    Parameter *parameters;
    Py_ssize_t count;
    int extensible;        /* whether more arguments may follow the parameters */
    int named;             /* whether any parameter may be passed by name - cached from parameters */
    PyObject *description; /* borrowed from the spec data - how the errors describe the call */
} Signature;

static int
is_named(ParameterKind kind)
{
    return kind == PARAMETER_POSITIONAL_OR_KEYWORD || kind == PARAMETER_KEYWORD_ONLY;
}

/* parameters of a description - ((name, kind, defaulted), ...) - borrowing the names. The
 * buffer leaves `reserve` free slots before them and `tail` free slots after them */
static Parameter *
signature_parameters(PyObject *described, Py_ssize_t reserve, Py_ssize_t tail, Py_ssize_t *count)
{
    if (!PyTuple_Check(described)) {
        PyErr_SetString(PyExc_TypeError, "parameters are described by a tuple");
        return NULL;
    }
    Py_ssize_t n = PyTuple_GET_SIZE(described);
    Py_ssize_t size = reserve + n + tail;
    Parameter *parameters = PyMem_RawCalloc((size_t)(size > 0 ? size : 1), sizeof(Parameter));
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
        Parameter *parameter = &parameters[reserve + i];
        parameter->name = name == Py_None ? NULL : name;
        parameter->kind = (ParameterKind)kind;
        parameter->defaulted = PyTuple_GET_ITEM(item, 2) == Py_True;
    }
    *count = n;
    return parameters;
}

/* a signature of a description - (parameters, extensible, description) - borrowing from it,
 * so the description has to outlive the signature */
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
    signature->parameters = signature_parameters(PyTuple_GET_ITEM(described, 0), 0, 0, &signature->count);
    if (signature->parameters == NULL) {
        PyMem_RawFree(signature);
        return NULL;
    }
    signature->extensible = PyTuple_GET_ITEM(described, 1) == Py_True;
    for (Py_ssize_t i = 0; i < signature->count; i++) {
        if (is_named(signature->parameters[i].kind)) signature->named = 1;
    }
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

/* 1 when both are the same name, 0 when either is NULL or they differ, -1 on an error */
static int
same_name(PyObject *first, PyObject *second)
{
    if (first == NULL || second == NULL) return 0;
    if (first == second) return 1;
    return PyUnicode_Equal(first, second);
}

static int
has_parameter(const Parameter *parameters, Py_ssize_t count, ParameterKind kind)
{
    for (Py_ssize_t i = 0; i < count; i++) {
        if (parameters[i].kind == kind) return 1;
    }
    return 0;
}

/* the number of leading parameters accepting arguments by position */
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

/* whether the expected parameter at `index` is passed by name when the ones before
 * `expected_positional` are passed by position */
static int
passed_by_name(const Parameter *expected, Py_ssize_t index, Py_ssize_t expected_positional)
{
    return index < expected_positional ? expected[index].kind == PARAMETER_POSITIONAL_OR_KEYWORD
                                       : expected[index].kind == PARAMETER_KEYWORD_ONLY;
}

/* whether the parameters accept an argument passed by name, with `given` arguments passed by
 * position - 1, 0, or -1 on an error. A parameter of that name decides: it refuses the name
 * when it already got an argument by position, from this call or from a partial or bound
 * method - **kwargs never takes it then. Only a name no parameter has goes to **kwargs - also
 * the name of a positional only parameter */
static int
name_taken(const Parameter *taking, Py_ssize_t taking_count, int any_keywords, PyObject *name, Py_ssize_t given)
{
    for (Py_ssize_t i = 0; i < taking_count; i++) {
        const Parameter *parameter = &taking[i];
        if (!is_named(parameter->kind) && parameter->kind != PARAMETER_GIVEN) continue;
        int same = same_name(parameter->name, name);
        if (same < 0) return -1;
        if (!same) continue;
        if (parameter->kind == PARAMETER_GIVEN) return 0;
        return parameter->kind == PARAMETER_KEYWORD_ONLY || i >= given;
    }
    return any_keywords;
}

/* whether every call passes the name - an expected parameter of that name, past the first
 * `positional` passed by position, is passed by name and has no default. 1, 0, or -1 */
static int
always_named(const Parameter *expected, Py_ssize_t expected_count, Py_ssize_t expected_positional,
    Py_ssize_t positional, PyObject *name)
{
    for (Py_ssize_t i = positional; i < expected_count; i++) {
        if (!passed_by_name(expected, i, expected_positional) || expected[i].defaulted) continue;
        int same = same_name(expected[i].name, name);
        if (same != 0) return same;
    }
    return 0;
}

/* whether the parameters accept the calls passing the first `positional` expected parameters
 * by position, `given` arguments by position in total, and the rest by name or not at all -
 * 1, 0, or -1 on an error. Each name passed has to be accepted on its own, and each required
 * parameter has to get an argument, so checking the names which may be passed and the names
 * always passed covers every combination of the optional ones */
static int
calls_taken(const Parameter *taking, Py_ssize_t taking_count, const Parameter *expected, Py_ssize_t expected_count,
    Py_ssize_t positional, Py_ssize_t given)
{
    Py_ssize_t taking_positional = positional_count(taking, taking_count);
    Py_ssize_t expected_positional = positional_count(expected, expected_count);
    int any_keywords = has_parameter(taking, taking_count, PARAMETER_VAR_KEYWORD);
    if (given > taking_positional && !has_parameter(taking, taking_count, PARAMETER_VAR_POSITIONAL)) return 0;

    for (Py_ssize_t i = positional; i < expected_count; i++) {
        if (!passed_by_name(expected, i, expected_positional)) continue;
        int r = name_taken(taking, taking_count, any_keywords, expected[i].name, given);
        if (r <= 0) return r;
    }
    for (Py_ssize_t i = 0; i < taking_count; i++) {
        const Parameter *parameter = &taking[i];
        if (parameter->defaulted) continue;
        if (i < taking_positional) {
            if (i < given) continue; /* passed by position */
            if (parameter->kind == PARAMETER_POSITIONAL_ONLY) return 0;
        } else if (parameter->kind != PARAMETER_KEYWORD_ONLY) {
            continue; /* *args and **kwargs require nothing */
        }
        int r = always_named(expected, expected_count, expected_positional, positional, parameter->name);
        if (r <= 0) return r;
    }
    return 1;
}

/* the fewest expected positional parameters passed by position - each one after it can be
 * passed by name or left out for its default */
static Py_ssize_t
fewest_positional(const Parameter *expected, Py_ssize_t expected_positional)
{
    Py_ssize_t fewest = expected_positional;
    while (fewest > 0
        && (expected[fewest - 1].kind == PARAMETER_POSITIONAL_OR_KEYWORD || expected[fewest - 1].defaulted)) {
        fewest--;
    }
    return fewest;
}

/* whether the parameters accept the calls of expected parameters which more arguments may
 * follow - those of a ParamSpec, which may be anything. The expected parameters have to fit
 * by position and by name, while the rest may pass whatever else the parameters require, so
 * nothing is required. A name is harder to accept after more positional arguments, so each
 * one is checked at its own position - 1, 0, or -1 on an error */
static int
open_calls_taken(const Parameter *taking, Py_ssize_t taking_count, const Parameter *expected,
    Py_ssize_t expected_count)
{
    Py_ssize_t taking_positional = positional_count(taking, taking_count);
    Py_ssize_t expected_positional = positional_count(expected, expected_count);
    if (expected_positional > taking_positional && !has_parameter(taking, taking_count, PARAMETER_VAR_POSITIONAL)) {
        return 0;
    }
    int any_keywords = has_parameter(taking, taking_count, PARAMETER_VAR_KEYWORD);
    for (Py_ssize_t i = fewest_positional(expected, expected_positional); i < expected_positional; i++) {
        if (expected[i].kind != PARAMETER_POSITIONAL_OR_KEYWORD) continue;
        int r = name_taken(taking, taking_count, any_keywords, expected[i].name, i);
        if (r <= 0) return r;
    }
    return 1;
}

/* whether the parameters accept every call of the expected ones - 1, 0, or -1 on an error.
 * Every split of the expected positional parameters is tried - the first ones passed by
 * position, the rest by name or left out - and with *args, more positional arguments than
 * any parameter takes. When more arguments may follow, the expected ones only have to fit */
static int
signature_fits(const Parameter *taking, Py_ssize_t taking_count, const Parameter *expected, Py_ssize_t expected_count,
    int extensible)
{
    if (extensible) return open_calls_taken(taking, taking_count, expected, expected_count);
    if (has_parameter(expected, expected_count, PARAMETER_VAR_KEYWORD)
        && !has_parameter(taking, taking_count, PARAMETER_VAR_KEYWORD)) {
        return 0; /* may be passed a name nothing accepts */
    }
    Py_ssize_t taking_positional = positional_count(taking, taking_count);
    Py_ssize_t expected_positional = positional_count(expected, expected_count);
    for (Py_ssize_t positional = fewest_positional(expected, expected_positional); positional <= expected_positional;
        positional++) {
        int r = calls_taken(taking, taking_count, expected, expected_count, positional, positional);
        if (r <= 0) return r;
    }
    if (has_parameter(expected, expected_count, PARAMETER_VAR_POSITIONAL)) {
        Py_ssize_t more = (taking_positional > expected_positional ? taking_positional : expected_positional) + 1;
        int r = calls_taken(taking, taking_count, expected, expected_count, expected_positional, more);
        if (r <= 0) return r;
    }
    return 1;
}

/* the length of a function attribute, 0 for None - -1 on an error */
static Py_ssize_t
function_values(PyObject *function, PyObject *name)
{
    PyObject *values = PyObject_GetAttr(function, name);
    if (values == NULL) return -1;
    Py_ssize_t count = values == Py_None ? 0 : PyObject_Length(values);
    Py_DECREF(values);
    return count;
}

/* the arguments partials and bound methods pass before the call does: the first `stored`
 * positions, `holes` of them Placeholders which the call fills first, by position. Which
 * positions are holes does not matter - the call fills them in order, then continues after
 * the stored ones */
typedef struct {
    Py_ssize_t stored;
    Py_ssize_t holes;
    PyObject *keywords; /* an owned dict of the arguments passed by name, NULL for none */
} Given;

/* the parameters of a function, read from its code, in a buffer with `reserve` free slots
 * before them and room for up to `stored` more after them. Names are read only when
 * `naming` - without them a keyword only parameter is defaulted only when every one is,
 * which is equivalent when nothing is passed by name. The buffer is `local` when its `room`
 * is enough, otherwise allocated with PyMem_RawCalloc. The parameters borrow their names from
 * `*names` (a new reference or NULL), their count goes to `*count` - NULL on an error */
static Parameter *
code_parameters(ModuleState *state, PyObject *function, Py_ssize_t reserve, Py_ssize_t stored, int naming,
    Parameter *local, Py_ssize_t room, Py_ssize_t *count, PyObject **names)
{
    *names = NULL;
    /* the attributes are read through Python - any of them may be replaced concurrently */
    PyObject *code = PyObject_GetAttr(function, state->str_code);
    if (code == NULL) return NULL;
    if (!PyCode_Check(code)) {
        Py_DECREF(code);
        PyErr_SetString(PyExc_TypeError, "the code of a function is not a code object");
        return NULL;
    }
    PyCodeObject *co = (PyCodeObject *)code;
    Py_ssize_t positional = co->co_argcount;
    Py_ssize_t keyword = co->co_kwonlyargcount;
    Py_ssize_t posonly = co->co_posonlyargcount;
    int variadic = (co->co_flags & CO_VARARGS) != 0;
    int any_keywords = (co->co_flags & CO_VARKEYWORDS) != 0;
    if (naming) {
        *names = PyCode_GetVarnames(co);
        if (*names == NULL) {
            Py_DECREF(code);
            return NULL;
        }
    }
    Py_DECREF(code);
    if (*names != NULL && PyTuple_GET_SIZE(*names) < positional + keyword) {
        PyErr_SetString(PyExc_TypeError, "the code of a function names fewer arguments than it takes");
        goto failed;
    }
    Py_ssize_t defaults = function_values(function, state->str_defaults);
    if (defaults < 0) goto failed;
    PyObject *keyword_defaults = NULL;
    if (keyword > 0) {
        keyword_defaults = PyObject_GetAttr(function, state->str_kwdefaults);
        if (keyword_defaults == NULL) goto failed;
    }
    /* the parameters, *args and **kwargs, and a slot for each positional one passed already */
    Py_ssize_t size = reserve + positional + keyword + 2 + (stored < positional ? stored : positional);
    Parameter *base = local;
    if (size > room) {
        base = PyMem_RawCalloc((size_t)size, sizeof(Parameter));
        if (base == NULL) {
            Py_XDECREF(keyword_defaults);
            PyErr_NoMemory();
            goto failed;
        }
    } else {
        memset(local, 0, (size_t)size * sizeof(Parameter));
    }
    Parameter *parameters = base + reserve;
    Py_ssize_t n = 0;
    for (Py_ssize_t i = 0; i < positional; i++, n++) {
        parameters[n].name = *names != NULL ? PyTuple_GET_ITEM(*names, i) : NULL;
        parameters[n].kind = i < posonly ? PARAMETER_POSITIONAL_ONLY : PARAMETER_POSITIONAL_OR_KEYWORD;
        parameters[n].defaulted = i >= positional - defaults;
    }
    if (variadic) parameters[n++].kind = PARAMETER_VAR_POSITIONAL;
    int every_keyword_defaulted = 0;
    if (keyword > 0 && *names == NULL) {
        Py_ssize_t defaulted = keyword_defaults == Py_None ? 0 : PyObject_Length(keyword_defaults);
        if (defaulted < 0) {
            Py_DECREF(keyword_defaults);
            goto released;
        }
        every_keyword_defaulted = defaulted >= keyword;
    }
    for (Py_ssize_t i = positional; i < positional + keyword; i++, n++) {
        parameters[n].kind = PARAMETER_KEYWORD_ONLY;
        if (*names == NULL) {
            parameters[n].defaulted = every_keyword_defaulted;
            continue;
        }
        parameters[n].name = PyTuple_GET_ITEM(*names, i);
        int defaulted = keyword_defaults != NULL && PyDict_Check(keyword_defaults)
            ? PyDict_Contains(keyword_defaults, parameters[n].name) : 0;
        if (defaulted < 0) {
            Py_XDECREF(keyword_defaults);
            goto released;
        }
        parameters[n].defaulted = defaulted;
    }
    Py_XDECREF(keyword_defaults);
    if (any_keywords) parameters[n++].kind = PARAMETER_VAR_KEYWORD;
    *count = n;
    return base;

released:
    if (base != local) PyMem_RawFree(base);
failed:
    Py_CLEAR(*names);
    return NULL;
}

/* the parameters left for the call after applying what partials and bound methods pass, in
 * place of the `count` parameters of `base`, which has `given->holes` free slots before them
 * and room after them for the ones passed by position. Each hole becomes a leading positional
 * only parameter, and a parameter passed by name becomes defaulted, turning each positional
 * parameter after it keyword only. The parameters passed by position are appended as
 * PARAMETER_GIVEN, so passing their names again fails. 1 with the result in `*taking`, 0 when
 * the arguments passed already fail every call, -1 on an error */
static int
given_parameters(Parameter *base, Py_ssize_t count, const Given *given, Parameter **taking, Py_ssize_t *taking_count)
{
    Parameter *parameters = base + given->holes;
    Py_ssize_t positional = positional_count(parameters, count);
    int any_keywords = has_parameter(parameters, count, PARAMETER_VAR_KEYWORD);
    if (given->stored > positional && !has_parameter(parameters, count, PARAMETER_VAR_POSITIONAL)) {
        return 0; /* too many positional arguments */
    }
    PyObject *keywords = given->keywords;
    if (keywords != NULL) { /* checked before the positions passed are dropped */
        Py_ssize_t matched = 0;
        for (Py_ssize_t i = 0; i < count; i++) {
            const Parameter *parameter = &parameters[i];
            /* a positional only parameter is never passed by name - its name goes to **kwargs, if any */
            if (!is_named(parameter->kind) || parameter->name == NULL) continue;
            int r = PyDict_Contains(keywords, parameter->name);
            if (r < 0) return -1;
            if (!r) continue;
            if (i < given->stored && i < positional) return 0; /* passed by position as well */
            matched += 1;
        }
        if (matched < PyDict_GET_SIZE(keywords) && !any_keywords) return 0;
    }
    /* drop the positions passed, putting the holes in their place */
    Py_ssize_t skipped = given->stored < positional ? given->stored : positional;
    Py_ssize_t blocked = 0;
    for (Py_ssize_t i = 0; i < skipped; i++) {
        if (parameters[i].kind != PARAMETER_POSITIONAL_OR_KEYWORD || parameters[i].name == NULL) continue;
        Parameter *passed = &parameters[count + blocked++];
        passed->name = parameters[i].name;
        passed->kind = PARAMETER_GIVEN;
        passed->defaulted = 1;
    }
    Parameter *left = parameters + skipped - given->holes;
    for (Py_ssize_t i = 0; i < given->holes; i++) {
        left[i].name = NULL;
        left[i].kind = PARAMETER_POSITIONAL_ONLY;
        left[i].defaulted = 0;
    }
    Py_ssize_t n = count - skipped + given->holes;
    *taking = left;
    *taking_count = n + blocked;
    if (keywords == NULL) return 1;

    int named = 0; /* whether a positional parameter was passed by name - so are the ones after it */
    for (Py_ssize_t i = given->holes; i < n; i++) {
        Parameter *parameter = &left[i];
        if (parameter->kind == PARAMETER_VAR_POSITIONAL && named) {
            parameter->kind = PARAMETER_NONE; /* any argument would pass the named one twice */
            continue;
        }
        /* *args and **kwargs have names in the code, but are never passed by them */
        if (parameter->name == NULL || !is_named(parameter->kind)) continue;
        int r = PyDict_Contains(keywords, parameter->name);
        if (r < 0) return -1;
        if (r) {
            named = named || parameter->kind == PARAMETER_POSITIONAL_OR_KEYWORD;
            parameter->kind = PARAMETER_KEYWORD_ONLY;
            parameter->defaulted = 1;
        } else if (named && parameter->kind == PARAMETER_POSITIONAL_OR_KEYWORD) {
            parameter->kind = PARAMETER_KEYWORD_ONLY;
        }
    }
    return 1;
}

/* what unwrapping partials and bound methods ends with */
typedef enum {
    UNWRAP_FAILED = -1, /* an error is set */
    UNWRAP_FOREIGN,     /* a partial not made by functools - read whole by inspect */
    UNWRAP_REFUSED,     /* no call succeeds - a keyword which is not a string, or a cycle */
    UNWRAP_CALLEE,      /* the callable called in the end */
} Unwrapped;

/* joins the keywords of a partial with those of the partials around it - the outer ones win,
 * as functools merges them */
static Unwrapped
given_keywords(Given *given, PyObject *keywords)
{
    if (!PyDict_Check(keywords)) return UNWRAP_FOREIGN;
    if (PyDict_GET_SIZE(keywords) == 0) return UNWRAP_CALLEE;
    PyObject *joined = PyDict_Copy(keywords); /* a copy nothing else changes while it is read */
    if (joined == NULL) return UNWRAP_FAILED;
    if (given->keywords != NULL && PyDict_Update(joined, given->keywords) < 0) {
        Py_DECREF(joined);
        return UNWRAP_FAILED;
    }
    Py_XSETREF(given->keywords, joined);
    Py_ssize_t position = 0;
    PyObject *name;
    while (PyDict_Next(joined, &position, &name, NULL)) {
        if (!PyUnicode_Check(name)) return UNWRAP_REFUSED;
    }
    return UNWRAP_CALLEE;
}

/* unwraps partials and bound methods to the callable they call, with a new reference in
 * `*callee` and what they pass in `given`. A partial passes its arguments first and its
 * keywords by name, a bound method passes its object first. Unless UNWRAP_CALLEE is returned,
 * `*callee` is NULL and `given` is empty */
static Unwrapped
unwrap_partial(ModuleState *state, PyObject *value, Given *given, PyObject **callee)
{
    Unwrapped result = UNWRAP_CALLEE;
    *callee = Py_NewRef(value);
    for (Py_ssize_t depth = 0;; depth++) {
        if (depth > SIGNATURE_DEPTH) {
            result = UNWRAP_REFUSED;
            goto unwrapped;
        }
        if (Py_IS_TYPE(*callee, &PyMethod_Type)) {
            given->stored += 1; /* the bound object comes first */
            Py_SETREF(*callee, Py_NewRef(PyMethod_GET_FUNCTION(*callee)));
            continue;
        }
        if (!Py_IS_TYPE(*callee, (PyTypeObject *)state->partial)) return UNWRAP_CALLEE;
        PyObject *keywords = PyObject_GetAttr(*callee, state->str_keywords);
        if (keywords == NULL) {
            result = UNWRAP_FAILED;
            goto unwrapped;
        }
        result = given_keywords(given, keywords);
        Py_DECREF(keywords);
        if (result != UNWRAP_CALLEE) goto unwrapped;
        PyObject *arguments = PyObject_GetAttr(*callee, state->str_args);
        if (arguments == NULL) {
            result = UNWRAP_FAILED;
            goto unwrapped;
        }
        if (!PyTuple_Check(arguments)) {
            Py_DECREF(arguments);
            result = UNWRAP_FOREIGN;
            goto unwrapped;
        }
        Py_ssize_t stored = PyTuple_GET_SIZE(arguments), holes = 0;
        for (Py_ssize_t i = 0; i < stored; i++) {
            if (PyTuple_GET_ITEM(arguments, i) == state->placeholder) holes++;
        }
        Py_DECREF(arguments);
        /* the outer arguments fill the holes of this partial first, then follow its arguments */
        Py_ssize_t outer_stored = given->stored;
        given->holes += holes > outer_stored ? holes - outer_stored : 0;
        given->stored = stored + (outer_stored > holes ? outer_stored - holes : 0);
        Py_SETREF(*callee, PyObject_GetAttr(*callee, state->str_func));
        if (*callee == NULL) {
            result = UNWRAP_FAILED;
            goto unwrapped;
        }
    }

unwrapped:
    Py_CLEAR(*callee);
    Py_CLEAR(given->keywords);
    given->stored = given->holes = 0;
    return result;
}

/* the Python function an object is called through - the __call__ of its class, looked up
 * along the MRO as the call itself does, skipping the object. A new reference in `*function`
 * with the arguments it binds first in `*bound`, or NULL when it is not a Python function -
 * 0, or -1 on an error */
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
        *bound = 1; /* the object itself */
        return 0;
    }
    if (Py_IS_TYPE(found, &PyStaticMethod_Type)) {
        PyObject *wrapped = PyObject_GetAttrString(found, "__func__");
        Py_DECREF(found);
        if (wrapped == NULL) return -1;
        if (PyFunction_Check(wrapped)) *function = wrapped; /* binds nothing */
        else Py_DECREF(wrapped);
        return 0;
    }
    Py_DECREF(found); /* another kind of descriptor - left to inspect */
    return 0;
}

/* the Python function a callable calls in the end - a new reference, adding the arguments it
 * binds first to `given` - or NULL, with no error when it is not a Python function */
static PyObject *
called_function(ModuleState *state, PyObject *callee, Given *given)
{
    if (PyFunction_Check(callee)) return Py_NewRef(callee);
    if (PyType_Check(callee)) return NULL;
    PyObject *function = NULL;
    Py_ssize_t first = 0;
    if (class_call(state, Py_TYPE(callee), &function, &first) < 0) return NULL;
    given->stored += first;
    return function;
}

/* the parameters inspect reads of a callable which is not a Python function, laid out as
 * code_parameters lays them out. NULL with no error when it has no signature to read. The
 * parameters borrow their names from `*described` */
static Parameter *
read_parameters(ModuleState *state, PyObject *callee, Py_ssize_t reserve, Py_ssize_t stored, Py_ssize_t *count,
    PyObject **described)
{
    *described = PyObject_CallOneArg(state->parameters, callee);
    if (*described == NULL) return NULL;
    if (*described == Py_None) {
        Py_CLEAR(*described);
        return NULL;
    }
    if (!PyTuple_Check(*described)) {
        Py_CLEAR(*described);
        PyErr_SetString(PyExc_TypeError, "parameters are described by a tuple");
        return NULL;
    }
    Py_ssize_t read = PyTuple_GET_SIZE(*described);
    Parameter *parameters = signature_parameters(*described, reserve, stored < read ? stored : read, count);
    if (parameters == NULL) Py_CLEAR(*described);
    return parameters;
}

/* whether a callable accepts the calls of a signature - 1, 0, or -1 on an error. Not inlined,
 * as its locals would deepen every validation frame it was inlined into */
static Py_NO_INLINE int
signature_accepts(ModuleState *state, Signature *signature, PyObject *value)
{
    Given given = {0, 0, NULL};
    PyObject *callee;
    switch (unwrap_partial(state, value, &given, &callee)) {
    case UNWRAP_FAILED:
        return -1;
    case UNWRAP_REFUSED:
        return 0;
    case UNWRAP_FOREIGN:
        callee = Py_NewRef(value);
        break;
    case UNWRAP_CALLEE:
        break;
    }
    PyObject *function = called_function(state, callee, &given);
    if (function == NULL && PyErr_Occurred()) {
        Py_DECREF(callee);
        Py_XDECREF(given.keywords);
        return -1;
    }
    int r;
    Py_ssize_t count = 0;
    PyObject *names = NULL;
    Parameter local[SIGNATURE_LOCAL];
    Parameter *base;
    if (function != NULL) {
        base = code_parameters(state, function, given.holes, given.stored,
            signature->named || given.keywords != NULL, local, SIGNATURE_LOCAL, &count, &names);
    } else {
        base = read_parameters(state, callee, given.holes, given.stored, &count, &names);
    }
    if (base == NULL) {
        r = PyErr_Occurred() ? -1 : 1; /* no signature to read - accepted without a check */
    } else {
        Parameter *taking = NULL;
        Py_ssize_t taking_count = 0;
        r = given_parameters(base, count, &given, &taking, &taking_count);
        if (r > 0) {
            r = signature_fits(taking, taking_count, signature->parameters, signature->count, signature->extensible);
        }
        if (base != local) PyMem_RawFree(base);
    }
    Py_XDECREF(names);
    Py_XDECREF(function);
    Py_DECREF(callee);
    Py_XDECREF(given.keywords);
    return r;
}

/* signature_fits(parameters, expected, extensible) - compares parameters described in Python
 * the way the parameters of a callable are compared */
static PyObject *
signature_fits_native(PyObject *module, PyObject *const *args, Py_ssize_t nargs)
{
    if (nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "signature_fits(parameters, expected, extensible)");
        return NULL;
    }
    Py_ssize_t taking_count = 0, expected_count = 0;
    Parameter *taking = signature_parameters(args[0], 0, 0, &taking_count);
    if (taking == NULL) return NULL;
    Parameter *expected = signature_parameters(args[1], 0, 0, &expected_count);
    if (expected == NULL) {
        PyMem_RawFree(taking);
        return NULL;
    }
    int r = signature_fits(taking, taking_count, expected, expected_count, PyObject_IsTrue(args[2]) > 0);
    PyMem_RawFree(taking);
    PyMem_RawFree(expected);
    if (r < 0) return NULL;
    return PyBool_FromLong(r);
}
