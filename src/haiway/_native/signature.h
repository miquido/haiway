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
#define PARAMETER_GIVEN -2 /* a parameter given by position already - given its name again, a call fails */

typedef struct {
    PyObject *name; /* borrowed - NULL for a positional only parameter of a Callable */
    int kind;
    int defaulted;
} Parameter;

/* the parameters of a function read without allocating them - a leaf of validation, which
 * never nests in itself, so the frame of a deep validation does not grow with it */
#define SIGNATURE_LOCAL 12

typedef struct {
    Parameter *parameters;
    Py_ssize_t count;
    int extensible;        /* whether parameters not told may follow the ones told */
    int named;             /* whether any told parameter may be given by name */
    PyObject *description; /* borrowed - how the function is called, wording the errors */
} Signature;

/* the parameters of a description - ((name, kind, defaulted), ...) - borrowing the names, after
 * `reserve` slots left free in front of them and with `tail` more free after them */
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
        parameter->kind = (int)kind;
        parameter->defaulted = PyTuple_GET_ITEM(item, 2) == Py_True;
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
    signature->parameters = signature_parameters(PyTuple_GET_ITEM(described, 0), 0, 0, &signature->count);
    if (signature->parameters == NULL) {
        PyMem_RawFree(signature);
        return NULL;
    }
    signature->extensible = PyTuple_GET_ITEM(described, 1) == Py_True;
    for (Py_ssize_t i = 0; i < signature->count; i++) {
        int kind = signature->parameters[i].kind;
        if (kind == PARAMETER_POSITIONAL_OR_KEYWORD || kind == PARAMETER_KEYWORD_ONLY) signature->named = 1;
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
 * of the name takes it unless it was given by position already - by this call, or before it -
 * and the keywords of any name take what no parameter does, a name of a positional only
 * parameter included */
static int
name_taken(const Parameter *taking, Py_ssize_t taking_count, int any_keywords, PyObject *name, Py_ssize_t given)
{
    for (Py_ssize_t i = 0; i < taking_count; i++) {
        const Parameter *parameter = &taking[i];
        if (parameter->kind != PARAMETER_POSITIONAL_OR_KEYWORD && parameter->kind != PARAMETER_KEYWORD_ONLY
            && parameter->kind != PARAMETER_GIVEN) {
            continue;
        }
        int same = same_name(parameter->name, name);
        if (same < 0) return -1;
        if (!same) continue;
        if (parameter->kind == PARAMETER_GIVEN) return 0;
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

/* the fewest told positional parameters given by position - every one after it can be given
 * by name, or left out for its default */
static Py_ssize_t
fewest_positional(const Parameter *told, Py_ssize_t told_positional)
{
    Py_ssize_t fewest = told_positional;
    while (fewest > 0
        && (told[fewest - 1].kind == PARAMETER_POSITIONAL_OR_KEYWORD || told[fewest - 1].defaulted)) {
        fewest--;
    }
    return fewest;
}

/* whether parameters take the calls of told parameters which more may follow - those of a
 * parameter specification, of which nothing is known. The told ones have to fit, by position
 * and by name - what follows may give whatever else the parameters need, so none of them has
 * to be given. A name given past more positional arguments is the harder to take, so each is
 * tried given at its own position - 1, 0, or -1 on an error */
static int
open_calls_taken(const Parameter *taking, Py_ssize_t taking_count, const Parameter *told, Py_ssize_t told_count)
{
    Py_ssize_t taking_positional = positional_count(taking, taking_count);
    Py_ssize_t told_positional = positional_count(told, told_count);
    if (told_positional > taking_positional && !has_parameter(taking, taking_count, PARAMETER_VAR_POSITIONAL)) {
        return 0;
    }
    int any_keywords = has_parameter(taking, taking_count, PARAMETER_VAR_KEYWORD);
    for (Py_ssize_t i = fewest_positional(told, told_positional); i < told_positional; i++) {
        if (told[i].kind != PARAMETER_POSITIONAL_OR_KEYWORD) continue;
        int r = name_taken(taking, taking_count, any_keywords, told[i].name, i);
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
    if (extensible) return open_calls_taken(taking, taking_count, told, told_count);
    if (has_parameter(told, told_count, PARAMETER_VAR_KEYWORD)
        && !has_parameter(taking, taking_count, PARAMETER_VAR_KEYWORD)) {
        return 0; /* given a name nothing else takes */
    }
    Py_ssize_t fewest = fewest_positional(told, told_positional);
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

/* what partials - and the object a method is bound to - give a function before it is called:
 * its first `stored` positions are given, and `holes` of those - each a Placeholder - are given
 * by the call first, by position only. Which of the positions they are tells nothing more - the
 * call fills them in order, then the positions after the stored ones */
typedef struct {
    Py_ssize_t stored;
    Py_ssize_t holes;
    PyObject *keywords; /* a dict of its own, what is given by name - NULL for nothing */
} Given;

/* the parameters a function takes, read from its code - after `reserve` slots left free in
 * front of them, and with room after them for the first `stored` of them. Their names are read only when `naming` - without them a keyword only parameter
 * has a default only when every one has, which tells the same when nothing is given by name.
 * The parameters, in `local` when its `room` is enough, borrow from `*names` (a new reference or
 * NULL), their count in `*count` - NULL on an error */
static Parameter *
code_parameters(ModuleState *state, PyObject *function, Py_ssize_t reserve, Py_ssize_t stored, int naming,
    Parameter *local, Py_ssize_t room, Py_ssize_t *count, PyObject **names)
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
    Py_ssize_t size = reserve + positional + keyword + 2 + (stored < positional ? stored : positional);
    Parameter *base = local;
    if (size > room) {
        base = PyMem_Calloc((size_t)size, sizeof(Parameter));
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
    if (base != local) PyMem_Free(base);
failed:
    Py_CLEAR(*names);
    return NULL;
}

/* the parameters left to a call once what is given was given - in place of the `count` ones of
 * `base`, which has `given->holes` slots free in front of them and room for the given ones
 * after them. Each hole is a positional only parameter first, a parameter given by name has a
 * default, and every positional one after it can be given by name only. The ones given by
 * position follow the rest, their names failing a call giving them again. 1 with them in
 * `*taking`, 0 when it can't be called that way at all, -1 on an error */
static int
given_parameters(Parameter *base, Py_ssize_t count, const Given *given, Parameter **taking, Py_ssize_t *taking_count)
{
    Parameter *parameters = base + given->holes;
    Py_ssize_t positional = positional_count(parameters, count);
    int any_keywords = has_parameter(parameters, count, PARAMETER_VAR_KEYWORD);
    if (given->stored > positional && !has_parameter(parameters, count, PARAMETER_VAR_POSITIONAL)) {
        return 0; /* given more than it takes */
    }
    PyObject *keywords = given->keywords;
    if (keywords != NULL) { /* what is given by name - read before the given positions are left */
        Py_ssize_t matched = 0;
        for (Py_ssize_t i = 0; i < count; i++) {
            const Parameter *parameter = &parameters[i];
            /* a positional only parameter is never given by name - its name goes to the keywords
             * of any name, or nowhere */
            if (parameter->kind != PARAMETER_POSITIONAL_OR_KEYWORD && parameter->kind != PARAMETER_KEYWORD_ONLY) continue;
            if (parameter->name == NULL) continue;
            int r = PyDict_Contains(keywords, parameter->name);
            if (r < 0) return -1;
            if (!r) continue;
            if (i < given->stored && i < positional) return 0; /* given by position already */
            matched += 1;
        }
        if (matched < PyDict_GET_SIZE(keywords) && !any_keywords) return 0;
    }
    /* the given positions are left, and the holes come first in their place */
    Py_ssize_t skipped = given->stored < positional ? given->stored : positional;
    Py_ssize_t blocked = 0;
    for (Py_ssize_t i = 0; i < skipped; i++) {
        if (parameters[i].kind != PARAMETER_POSITIONAL_OR_KEYWORD || parameters[i].name == NULL) continue;
        parameters[count + blocked++] = (Parameter){.name = parameters[i].name, .kind = PARAMETER_GIVEN, .defaulted = 1};
    }
    Parameter *left = parameters + skipped - given->holes;
    for (Py_ssize_t i = 0; i < given->holes; i++) {
        left[i] = (Parameter){.name = NULL, .kind = PARAMETER_POSITIONAL_ONLY, .defaulted = 0};
    }
    Py_ssize_t n = count - skipped + given->holes;
    *taking = left;
    *taking_count = n + blocked;
    if (keywords == NULL) return 1;

    int named = 0; /* whether a positional parameter was given by name - the ones after it too */
    for (Py_ssize_t i = given->holes; i < n; i++) {
        Parameter *parameter = &left[i];
        if (parameter->kind == PARAMETER_VAR_POSITIONAL && named) {
            parameter->kind = PARAMETER_NONE; /* given anything, it gives the named one twice */
            continue;
        }
        if (parameter->name == NULL
            || (parameter->kind != PARAMETER_POSITIONAL_OR_KEYWORD && parameter->kind != PARAMETER_KEYWORD_ONLY)) {
            continue; /* never given by name - the variadic ones are named by Python, never taking it */
        }
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

/* the keywords of a partial joined with what partials around it give - those given later win,
 * as functools merges them. 1, 0 when they are not what a partial holds, -1 on an error */
static int
given_keywords(Given *given, PyObject *keywords)
{
    if (!PyDict_Check(keywords)) return 0;
    if (PyDict_GET_SIZE(keywords) == 0) return 1;
    PyObject *joined = PyDict_Copy(keywords); /* its own - nothing changes it while it is read */
    if (joined == NULL) return -1;
    if (given->keywords != NULL && PyDict_Update(joined, given->keywords) < 0) {
        Py_DECREF(joined);
        return -1;
    }
    Py_ssize_t position = 0;
    PyObject *name;
    while (PyDict_Next(joined, &position, &name, NULL)) {
        if (!PyUnicode_Check(name)) { /* a call refuses it whatever is called */
            Py_DECREF(joined);
            return 0;
        }
    }
    Py_XSETREF(given->keywords, joined);
    return 1;
}

/* a partial is the function it calls, given its arguments first and its keywords by name - a
 * new reference to what is called in the end, with what it is given in `given`. NULL on an
 * error, and with no error for a partial which is not what functools makes - left to Python */
static PyObject *
unwrap_partial(ModuleState *state, PyObject *value, Given *given)
{
    PyObject *callee = Py_NewRef(value);
    while (Py_IS_TYPE(callee, (PyTypeObject *)state->partial)) {
        PyObject *keywords = PyObject_GetAttr(callee, state->str_keywords);
        if (keywords == NULL) goto failed;
        int r = given_keywords(given, keywords);
        Py_DECREF(keywords);
        if (r <= 0) goto failed;
        PyObject *arguments = PyObject_GetAttr(callee, state->str_args);
        if (arguments == NULL) goto failed;
        if (!PyTuple_Check(arguments)) {
            Py_DECREF(arguments);
            goto failed;
        }
        Py_ssize_t stored = PyTuple_GET_SIZE(arguments), holes = 0;
        for (Py_ssize_t i = 0; i < stored; i++) {
            if (PyTuple_GET_ITEM(arguments, i) == state->placeholder) holes++;
        }
        Py_DECREF(arguments);
        /* what was given around it fills its holes first, and follows what it gives */
        Py_ssize_t outer_stored = given->stored;
        given->holes += holes > outer_stored ? holes - outer_stored : 0;
        given->stored = stored + (outer_stored > holes ? outer_stored - holes : 0);
        PyObject *function = PyObject_GetAttr(callee, state->str_func);
        Py_SETREF(callee, function);
        if (callee == NULL) goto failed;
    }
    return callee;

failed:
    Py_XDECREF(callee);
    Py_CLEAR(given->keywords);
    given->stored = given->holes = 0;
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

/* the function a callable calls in the end, written in Python - a new reference, with what is
 * given to it first added to `given` - or NULL, with no error when it is not such a function */
static PyObject *
called_function(ModuleState *state, PyObject *callee, Given *given)
{
    if (PyFunction_Check(callee)) return Py_NewRef(callee);
    if (Py_IS_TYPE(callee, &PyMethod_Type)) {
        PyObject *function = PyMethod_GET_FUNCTION(callee); /* a method never changes what it binds */
        if (!PyFunction_Check(function)) return NULL;
        given->stored += 1; /* what it is bound to comes first */
        return Py_NewRef(function);
    }
    if (PyType_Check(callee)) return NULL;
    PyObject *function = NULL;
    Py_ssize_t first = 0;
    if (class_call(state, Py_TYPE(callee), &function, &first) < 0) return NULL;
    given->stored += first;
    return function;
}

/* the parameters Python reads of a callable which is not a function - its signature, of which
 * what is given is left here, as it is for a function. NULL with no error when it has none to
 * read, the description it borrows from in `*described` */
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

/* whether a callable can be called the way a signature tells - 1, 0, or -1 on an error. Kept
 * out of line - inlined into validation, its parameters would deepen every frame of it */
static Py_NO_INLINE int
signature_accepts(ModuleState *state, Signature *signature, PyObject *described, PyObject *value)
{
    Given given = {0, 0, NULL};
    PyObject *callee = unwrap_partial(state, value, &given);
    if (callee == NULL) {
        if (PyErr_Occurred()) return -1;
        callee = Py_NewRef(value); /* a partial of its own kind - all of it read by Python */
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
        r = PyErr_Occurred() ? -1 : 1; /* a callable without a signature to read is not refused */
    } else {
        Parameter *taking = NULL;
        Py_ssize_t taking_count = 0;
        r = given_parameters(base, count, &given, &taking, &taking_count);
        if (r > 0) {
            r = signature_fits(taking, taking_count, signature->parameters, signature->count, signature->extensible);
        }
        if (function == NULL) PyMem_RawFree(base);
        else if (base != local) PyMem_Free(base);
    }
    Py_XDECREF(names);
    Py_XDECREF(function);
    Py_DECREF(callee);
    Py_XDECREF(given.keywords);
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
    Parameter *taking = signature_parameters(args[0], 0, 0, &taking_count);
    if (taking == NULL) return NULL;
    Parameter *told = signature_parameters(args[1], 0, 0, &told_count);
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
