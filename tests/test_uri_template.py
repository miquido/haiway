import pytest

import haiway
from haiway import URITemplate, ValidationError, helpers

TEMPLATE = "https://{tenant}.example.com/files{/name}{?page,tag}{#section}"


def test_variables_keep_template_order() -> None:
    assert URITemplate.of(TEMPLATE).variables == ("tenant", "name", "page", "tag", "section")


def test_expand_encodes_and_omits_optional_expressions() -> None:
    template = URITemplate.of(TEMPLATE)

    assert (
        template.expand({"tenant": "acme", "name": "a b.txt"})
        == "https://acme.example.com/files/a%20b.txt"
    )
    assert (
        template.expand({"tenant": "acme", "name": "x", "page": 2, "section": "top 1"})
        == "https://acme.example.com/files/x?page=2#top%201"
    )


def test_expand_sequences_and_existing_query() -> None:
    assert (
        URITemplate.of("https://x.test/a?fixed=1{?tag}").expand({"tag": ["a", "b"]})
        == "https://x.test/a?fixed=1&tag=a&tag=b"
    )
    assert URITemplate.of("/items/{ids}").expand({"ids": [1, 2]}) == "/items/1,2"


@pytest.mark.parametrize("values", [{}, {"id": None}, {"id": ""}])
def test_expand_requires_path_variables(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        URITemplate.of("/items/{id}").expand(values)


@pytest.mark.parametrize(
    "template",
    [
        "/items/{id",
        "/items/id}",
        "/items/{+id}",
        "/items/{a,b}",
        "/items/{}",
        "/items/{id}/{id}",
        "/items{#a}{?b}",
        "/items#x{?b}",
        "/items{#a}{#b}",
        "/items{?a}{?b}",
    ],
)
def test_invalid_templates_are_rejected(template: str) -> None:
    with pytest.raises(ValidationError):
        URITemplate.of(template)


def test_match_extracts_decoded_parameters() -> None:
    assert URITemplate.of(TEMPLATE).match(
        "https://acme.example.com/files/a%20b.txt?page=2&other=1&tag=x&tag=y#top%201"
    ) == {
        "tenant": "acme",
        "name": "a b.txt",
        "page": "2",
        "tag": "x",
        "section": "top 1",
    }


def test_match_without_optional_parts() -> None:
    assert URITemplate.of(TEMPLATE).match("https://acme.example.com/files/a") == {
        "tenant": "acme",
        "name": "a",
    }


@pytest.mark.parametrize(
    "uri",
    [
        "http://acme.example.com/files/a",
        "https://acme.example.org/files/a",
        "https://acme.example.com/files",
        "https://acme.example.com/files/a/b",
        "https://acme.example.com/files/..%2Fsecret",
        "https://acme.example.com/files/%2E%2E",
        "https://acme.example.com/files/a%5Cb",
        "https://evil.com@acme.example.com/files/a",
        "https://evil.com%40acme.example.com/files/a",
    ],
)
def test_match_rejects_foreign_and_unsafe_uris(uri: str) -> None:
    template = URITemplate.of(TEMPLATE)

    assert template.match(uri) is None
    assert not template.matches(uri)


def test_literal_template_matches_only_itself() -> None:
    template = URITemplate.of("https://x.test/a?b=1")

    assert template.matches("https://x.test/a?b=1")
    assert not template.matches("https://x.test/a?b=2")


def test_expand_then_match_roundtrip() -> None:
    template = URITemplate.of(TEMPLATE)
    values = {"tenant": "acme", "name": "a b", "page": "3", "section": "s"}

    assert template.match(template.expand(values)) == values


def test_public_interface_is_exported() -> None:
    assert haiway.URITemplate is helpers.URITemplate
    assert "URITemplate" in haiway.__all__
    assert "URITemplate" in helpers.__all__


def test_direct_construction_is_verified() -> None:
    with pytest.raises(ValidationError):
        URITemplate(template="/items/{id")

    assert URITemplate(template="/items/{id}") == URITemplate.of("/items/{id}")


def test_template_is_immutable_hashable_and_printable() -> None:
    template = URITemplate.of("/items/{id}")

    with pytest.raises(AttributeError):
        template.template = "/other"  # pyright: ignore[reportAttributeAccessIssue]

    assert hash(template) == hash(URITemplate.of("/items/{id}"))
    assert str(template) == "/items/{id}"


def test_match_result_is_immutable() -> None:
    matched = URITemplate.of("/items/{id}").match("/items/1")

    assert matched is not None
    with pytest.raises(AttributeError):
        matched["id"] = "2"  # pyright: ignore[reportIndexIssue]


@pytest.mark.parametrize(
    ("template", "values", "expected"),
    [
        ("/a{?x,y}", {"x": "1", "y": "2"}, "/a?x=1&y=2"),
        ("/a{?x, y}", {"y": "2"}, "/a?y=2"),
        ("/a{?x}", {"x": ""}, "/a"),
        ("/a{?x}", {"x": None}, "/a"),
        ("/a{?x}", {"x": [1, None, 2]}, "/a?x=1&x=2"),
        ("/a{?x}", {"x": True}, "/a?x=true"),
        ("/a{?x}", {"x": "a b&c=d"}, "/a?x=a+b%26c%3Dd"),
        ("/a?k=v{?x}", {"x": "1"}, "/a?k=v&x=1"),
        ("/a{/x}", {"x": "é ü"}, "/a/%C3%A9%20%C3%BC"),
        ("/a{#x}", {"x": "a b"}, "/a#a%20b"),
        ("/a{#x}", {}, "/a"),
        ("/{a}/{b}", {"a": 1, "b": False}, "/1/false"),
        ("mailto:{user}", {"user": "a@b.c"}, "mailto:a%40b.c"),
        ("https://x.test:8080/{a}", {"a": "b"}, "https://x.test:8080/b"),
    ],
)
def test_expand_cases(template: str, values: dict[str, object], expected: str) -> None:
    assert URITemplate.of(template).expand(values) == expected


@pytest.mark.parametrize(
    ("template", "uri", "expected"),
    [
        ("/a/{x}", "/a/1", {"x": "1"}),
        ("/a/{x}", "/a/%C3%A9", {"x": "é"}),
        ("/a/{x}.txt", "/a/b.txt", {"x": "b"}),
        ("/a{/x}{/y}", "/a/1/2", {"x": "1", "y": "2"}),
        ("/a{?x}", "/a?x=a+b", {"x": "a b"}),
        ("/a{?x}", "/a?x=1&x=2", {"x": "1"}),
        ("/a{?x}", "/a?y=1", {}),
        ("/a{?x}", "/a?", {}),
        ("/a{#x}", "/a#a%20b", {"x": "a b"}),
        ("/a?k=v{?x}", "/a?k=v&x=1", {"x": "1"}),
        ("https://x.test:8080/{a}", "https://x.test:8080/b", {"a": "b"}),
        ("https://{tenant}.example.com/x", "https://acme.example.com/x", {"tenant": "acme"}),
        ("/a/{x}", "/a/1\n", {"x": "1\n"}),
    ],
)
def test_match_cases(template: str, uri: str, expected: dict[str, str]) -> None:
    assert URITemplate.of(template).match(uri) == expected


@pytest.mark.parametrize(
    ("template", "uri"),
    [
        ("/a/{x}", "/a/"),
        ("/a/{x}", "/a//"),
        ("/a/{x}", "/b/1"),
        ("/a/{x}", "/a/1/2"),
        ("/a{/x}", "/a"),
        ("/a", "/a/"),
        ("/a", "/A"),
        ("https://x.test/{a}", "https://x.test:8080/b"),
        ("https://{tenant}.example.com/x", "https://a/b.example.com/x"),
        ("https://{tenant}.example.com/x", "https://a@b.example.com/x"),
        ("https://{tenant}.example.com/x", "https://a%40b.example.com/x"),
    ],
)
def test_match_misses(template: str, uri: str) -> None:
    assert URITemplate.of(template).match(uri) is None


def test_no_variables_template() -> None:
    template = URITemplate.of("https://x.test/static")

    assert template.variables == ()
    assert template.expand({}) == "https://x.test/static"
    assert template.match("https://x.test/static") == {}


def test_expand_ignores_unknown_values() -> None:
    assert URITemplate.of("/a/{x}").expand({"x": 1, "y": 2}) == "/a/1"


@pytest.mark.parametrize("value", ["a", "a b", "a%2Fb", "é", "a@b", "a?b#c", "a&b=c", "a+b"])
@pytest.mark.parametrize("length", [1, 500])
def test_expand_then_match_roundtrips_any_safe_value(value: str, length: int) -> None:
    template = URITemplate.of("https://{host}.example.com/files{/name}{?page}{#section}")
    value = value * length
    values = {"host": "acme", "name": value, "page": value, "section": value}

    assert template.match(template.expand(values)) == values


@pytest.mark.parametrize("value", ["a/b", "a\\b", ".", "..", "/"])
def test_expand_refuses_values_match_would_refuse(value: str) -> None:
    with pytest.raises(ValueError):
        URITemplate.of("/files{/name}").expand({"name": value})

    with pytest.raises(ValueError):
        URITemplate.of("/files/{name}").expand({"name": value})


def test_expand_refuses_user_info_within_host() -> None:
    template = URITemplate.of("https://{host}.example.com/x")

    with pytest.raises(ValueError):
        template.expand({"host": "evil.com@acme"})

    assert template.expand({"host": "a-b"}) == "https://a-b.example.com/x"
    # outside of the host the same character is plain data
    assert URITemplate.of("/u/{name}").expand({"name": "a@b"}) == "/u/a%40b"
