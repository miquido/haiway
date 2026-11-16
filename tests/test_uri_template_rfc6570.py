from collections.abc import Sequence

import pytest

from haiway import URITemplate, ValidationError

# the variables of RFC 6570, section 3.2 - undef stands for an undefined one
VARIABLES: dict[str, str | Sequence[str] | None] = {
    "count": ["one", "two", "three"],
    "dom": ["example", "com"],
    "dub": "me/too",
    "hello": "Hello World!",
    "half": "50%",
    "var": "value",
    "who": "fred",
    "base": "http://example.com/home/",
    "path": "/foo/bar",
    "list": ["red", "green", "blue"],
    "v": "6",
    "x": "1024",
    "y": "768",
    "empty": "",
    "empty_list": [],
    "year": ["1965", "2000", "2012"],
    "q": "cat",
    "lang": "en",
    "undef": None,
}


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        # 3.2.2 simple string expansion
        ("{var}", "value"),
        ("{hello}", "Hello%20World%21"),
        ("{half}", "50%25"),
        ("{who}", "fred"),
        ("{v}", "6"),
        ("{count}", "one,two,three"),
        ("{list}", "red,green,blue"),
        # 3.2.4 fragment expansion
        ("{#var}", "#value"),
        ("{#hello}", "#Hello%20World!"),
        ("{#half}", "#50%25"),
        ("{#list}", "#red,green,blue"),
        ("{#path}", "#/foo/bar"),
        ("{#base}", "#http://example.com/home/"),
        ("foo{#empty}", "foo#"),
        ("foo{#undef}", "foo"),
        ("foo{#empty_list}", "foo"),
        # 3.2.6 path segment expansion
        ("{/var}", "/value"),
        ("{/who}", "/fred"),
        ("{/hello}", "/Hello%20World%21"),
        ("{/half}", "/50%25"),
        ("{/list}", "/red,green,blue"),
        ("{/var}/here", "/value/here"),
        ("{/who}{/var}", "/fred/value"),
        # 3.2.8 form-style query expansion
        ("{?who}", "?who=fred"),
        ("{?half}", "?half=50%25"),
        ("{?hello}", "?hello=Hello%20World%21"),
        ("{?x,y}", "?x=1024&y=768"),
        ("{?x,y,empty}", "?x=1024&y=768&empty="),
        ("{?x,y,undef}", "?x=1024&y=768"),
        ("{?undef}", ""),
        ("{?empty_list}", ""),
        ("{?list}", "?list=red,green,blue"),
        ("{?list*}", "?list=red&list=green&list=blue"),
        ("{?x,list*}", "?x=1024&list=red&list=green&list=blue"),
        ("{?year*}", "?year=1965&year=2000&year=2012"),
        # 1.2 and 1.5 examples
        ("http://example.com/~{who}/", "http://example.com/~fred/"),
        ("http://example.com/{who}{?x,y}", "http://example.com/fred?x=1024&y=768"),
        ("http://example.com/search{?q,lang}", "http://example.com/search?q=cat&lang=en"),
        ("find{?year*}", "find?year=1965&year=2000&year=2012"),
        (
            "http://www.example.com/{who}/{v}{?q*,lang}",
            "http://www.example.com/fred/6?q=cat&lang=en",
        ),
        ("O{var}X", "OvalueX"),
    ],
)
def test_rfc_expansion_examples(template: str, expected: str) -> None:
    assert URITemplate.of(template).expand(VARIABLES) == expected


@pytest.mark.parametrize(
    ("template", "message"),
    [
        # the RFC expands an undefined variable to an empty string - a path or host variable
        # standing for nothing would address another resource, so it is required instead
        ("O{undef}X", "Missing"),
        ("O{empty}X", "Empty"),
        ("{/undef}", "Missing"),
        ("{/empty}", "Empty"),
        ("{/empty_list}", "Missing"),
        # the RFC percent-encodes a slash and hands it over (`me%2Ftoo`), once decoded it
        # crosses the segment the template declared
        ("{dub}", "Unsafe"),
        ("{/dub}", "Unsafe"),
        ("{path}", "Unsafe"),
        ("{base}", "Unsafe"),
    ],
)
def test_expansion_deviating_from_rfc_for_safety(template: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        URITemplate.of(template).expand(VARIABLES)


@pytest.mark.parametrize(
    "template",
    [
        # 3.2.3 reserved, 3.2.5 label, 3.2.7 matrix and 3.2.9 continuation expansion
        "{+var}",
        "{+path}/here",
        "{.var}",
        "X{.dom}",
        "{;who}",
        "{;x,y}",
        "{&who}",
        "?fixed=1{&x,y}",
        # level 4 prefix modifiers and explode outside of a query
        "{var:3}",
        "{?var:3}",
        "{list*}",
        "{/list*}",
        "{#list*}",
        # multiple variables outside of a query
        "{x,y}",
        "{/x,y}",
        "{#x,y}",
        # operators reserved for the future by the RFC
        "{=var}",
        "{,var}",
        "{!var}",
        "{@var}",
        "{|var}",
        # malformed expressions
        "{}",
        "{?}",
        "{/}",
        "{var",
        "var}",
        "{{var}}",
        "{va r}",
        "{ var}",
        "{var-name}",
        "{.}",
        "{a..b}",
        "{a.}",
        "{?x,}",
        "{?x,,y}",
    ],
)
def test_unsupported_and_malformed_expressions_are_rejected(template: str) -> None:
    with pytest.raises(ValidationError):
        URITemplate.of(template)


def test_dotted_variable_names_follow_the_rfc_varname() -> None:
    template = URITemplate.of("/users/{user.id}{?page.size}")

    assert template.variables == ("user.id", "page.size")
    assert template.expand({"user.id": 7, "page.size": 2}) == "/users/7?page.size=2"
    assert template.match("/users/7?page.size=2") == {"user.id": "7", "page.size": "2"}


@pytest.mark.parametrize(
    "value",
    [
        {"semi": ";"},
        {"semi"},
        frozenset({"a"}),
        b"bytes",
        bytearray(b"bytes"),
        [[1], [2]],
        [{"a": 1}],
    ],
)
@pytest.mark.parametrize(
    "template",
    ["/a/{x}", "/a{/x}", "/a{?x}", "/a{?x*}", "/a{#x}", "https://{x}.example.com/a"],
)
def test_expand_refuses_unsupported_value_types(template: str, value: object) -> None:
    # the RFC defines associative arrays, they are not supported - and their representation
    # must never make it into a URI as a string
    with pytest.raises(TypeError):
        URITemplate.of(template).expand({"x": value})


@pytest.mark.parametrize(
    "template",
    [
        "{var}",
        "{/var}",
        "{/who}{/var}",
        "{?x,y,empty}",
        "{?list}",
        "{?list*}",
        "{#hello}",
        "{#half}",
        "foo{#empty}",
        "http://example.com/~{who}/",
        "http://example.com/search{?q,lang}",
        "find{?year*}",
    ],
)
def test_rfc_examples_match_back_into_their_values(template: str) -> None:
    uri = URITemplate.of(template).expand(VARIABLES)
    matched = URITemplate.of(template).match(uri)

    assert matched is not None
    for name, value in matched.items():
        expected = VARIABLES[name]
        if isinstance(expected, list):
            # a list comes back as its comma separated form, or as its first element when
            # exploded into repeated keys
            assert value in (",".join(expected), expected[0])

        else:
            assert value == expected
