from collections import deque
from decimal import Decimal
from pathlib import PurePosixPath
from time import perf_counter
from uuid import UUID

import pytest

import haiway
from haiway import URITemplate, ValidationError, helpers
from haiway.helpers import uri_template as template_module

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
        == "https://x.test/a?fixed=1&tag=a,b"
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
        "/items/{ id}",
        "/a{?x, y}",
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
        ("/a{?x}", {"x": ""}, "/a?x="),
        ("/a{?x}", {"x": None}, "/a"),
        ("/a{?x}", {"x": [1, None, 2]}, "/a?x=1,2"),
        ("/a{?x*}", {"x": [1, None, 2]}, "/a?x=1&x=2"),
        ("/a{?x}", {"x": True}, "/a?x=true"),
        ("/a{?x}", {"x": "a b&c=d"}, "/a?x=a%20b%26c%3Dd"),
        ("/a?k=v{?x}", {"x": "1"}, "/a?k=v&x=1"),
        ("/a{/x}", {"x": "é ü"}, "/a/%C3%A9%20%C3%BC"),
        ("/a{#x}", {"x": "a b!"}, "/a#a%20b!"),
        ("/a{#x}", {"x": ""}, "/a#"),
        ("/a{#x}", {"x": []}, "/a"),
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
        ("/a{?x}", "/a?x=a%20b", {"x": "a b"}),
        ("/a{?x}", "/a?x=a+b", {"x": "a b"}),
        ("/a{?x}", "/a?x=1&x=2", {"x": "1"}),
        ("/a{?x}", "/a?y=1", {}),
        ("/a{?x}", "/a?", {}),
        ("/a{?x}", "/a?x=", {"x": ""}),
        ("/a{#x}", "/a#a%20b", {"x": "a b"}),
        ("/a?k=v{?x}", "/a?k=v&x=1", {"x": "1"}),
        ("https://x.test:8080/{a}", "https://x.test:8080/b", {"a": "b"}),
        ("https://{tenant}.example.com/x", "https://acme.example.com/x", {"tenant": "acme"}),
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


@pytest.mark.parametrize("value", ["a", "a b", "é", "a@b", "a?b#c", "a&b=c", "a+b", "5%"])
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


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.com%23.example.com/repos/lib",
        "https://evil.com%3F.example.com/repos/lib",
        "https://evil.com%40.example.com/repos/lib",
        "https://evil.com%2F.example.com/repos/lib",
    ],
)
def test_host_variable_never_decodes_into_a_delimiter(uri: str) -> None:
    assert URITemplate.of("https://{tenant}.example.com/repos{/repo}").match(uri) is None


@pytest.mark.parametrize("value", ["a#b", "a?b", "a@b", "a/b"])
def test_expand_refuses_delimiters_within_host(value: str) -> None:
    with pytest.raises(ValueError):
        URITemplate.of("https://{tenant}.example.com/x").expand({"tenant": value})


def test_host_variable_keeps_accepting_a_host_with_port() -> None:
    template = URITemplate.of("https://{host}/repos/{repo}")

    assert template.match("https://localhost:8080/repos/lib") == {
        "host": "localhost:8080",
        "repo": "lib",
    }
    assert template.expand({"host": "localhost:8080", "repo": "lib"}) == (
        "https://localhost%3A8080/repos/lib"
    )


@pytest.mark.parametrize(
    "template",
    [
        "/{a}{b}",
        "/{a}x{b}",
        "/{a}-{b}.txt",
        "https://{a}.{b}.example.com/x",
        "https://{a}.example.com:{port}/x",
        "/x?k={a}&j={b}",
        "/{/a}{b}",
        "/{a}{/b}{c}",
        "{/a}x{b}",
    ],
)
def test_several_variables_within_a_segment_are_rejected(template: str) -> None:
    with pytest.raises(ValidationError):
        URITemplate.of(template)


@pytest.mark.parametrize(
    "template",
    [
        "/{a}/{b}",
        "/{a}.txt",
        "/x-{a}",
        "https://{a}.example.com/{b}",
        "https://{a}.example.com{/b}{/c}",
        "{/a}{/b}",
        "/x?k={a}",
        "{a}/{b}",
        "/{a}{?b,c}{#d}",
    ],
)
def test_one_variable_per_segment_is_accepted(template: str) -> None:
    URITemplate.of(template)


@pytest.mark.parametrize(
    ("template", "uri"),
    [
        ("https://x.test/{a}.txt", "https://x.test/" + "a" * 50_000 + ".bin"),
        ("https://{a}.example.com/{b}/{c}", "https://" + "a" * 50_000 + ".example.org/x/y"),
        ("https://x.test/{a}/{b}/{c}/end", "https://x.test/" + "a/" * 20_000 + "z"),
        ("https://x.test/{a}{?q}", "https://x.test/" + "a" * 50_000 + "#" + "#" * 50_000),
        ("https://x.test/x{?q}{#f}", "https://x.test/x?" + "&" * 100_000 + "#" + "#" * 100_000),
    ],
)
def test_matching_adversarial_uris_is_linear(template: str, uri: str) -> None:
    started = perf_counter()
    URITemplate.of(template).match(uri)

    assert perf_counter() - started < 2


@pytest.mark.parametrize(
    "uri",
    [
        "https://acme.example.com/files/a%00b",
        "https://acme.example.com/files/a%0d%0ab",
        "https://acme.example.com/files/a%0Ab",
        "https://acme.example.com/files/a%7Fb",
        "https://acme.example.com/files/a\nb",
        "https://acme.example.com/files/a\x00b",
        "https://a%0d%0ab.example.com/files/x",
        "https://acme.example.com/files/x?page=a%00b",
        "https://acme.example.com/files/x?page=a%0d%0ab",
        "https://acme.example.com/files/x#a%0d%0ab",
    ],
)
def test_control_characters_never_reach_a_parameter(uri: str) -> None:
    assert URITemplate.of(TEMPLATE).match(uri) is None


@pytest.mark.parametrize("value", ["a\x00b", "a\nb", "a\x7fb", "a\rb"])
def test_expand_refuses_control_characters_within_path_values(value: str) -> None:
    with pytest.raises(ValueError):
        URITemplate.of("/f/{x}").expand({"x": value})


@pytest.mark.parametrize(
    "uri",
    [
        "https://acme.example.com/files/%ff",
        "https://acme.example.com/files/a%c0%afb",
        "https://acme.example.com/files/%e2%82",
        "https://%ff.example.com/files/x",
        "https://acme.example.com/files/x?page=%ff",
        "https://acme.example.com/files/x?page=%c0%af",
        "https://acme.example.com/files/x#%ff",
    ],
)
def test_malformed_utf8_never_reaches_a_parameter(uri: str) -> None:
    # decoding such a value would silently substitute a replacement character, or - for an
    # overlong form like `%c0%af` - be read as a slash by a more lenient decoder
    assert URITemplate.of(TEMPLATE).match(uri) is None


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.com%EF%BC%8F.example.com/files/x",  # fullwidth solidus
        "https://evil.com%EF%BC%A0.example.com/files/x",  # fullwidth commercial at
        "https://evil.com%EF%BC%83.example.com/files/x",  # fullwidth number sign
        "https://evil.com%EF%BC%9F.example.com/files/x",  # fullwidth question mark
        "https://a%20b.example.com/files/x",
        "https://a%09b.example.com/files/x",
        "https://a%C2%A0b.example.com/files/x",
    ],
)
def test_host_variable_refuses_look_alikes_and_whitespace(uri: str) -> None:
    assert URITemplate.of(TEMPLATE).match(uri) is None


def test_path_variable_keeps_non_delimiting_look_alikes() -> None:
    # outside of a host they are plain characters of a name
    matched = URITemplate.of("/files{/name}").match("/files/a%EF%BC%8Fb%20c")

    assert matched == {"name": f"a{chr(0xFF0F)}b c"}


def test_internationalized_host_label_is_accepted() -> None:
    template = URITemplate.of("https://{tenant}.example.com/x")

    assert template.match("https://b%C3%BCcher.example.com/x") == {"tenant": "bücher"}
    assert template.expand({"tenant": "bücher"}) == "https://b%C3%BCcher.example.com/x"


def test_scheme_relative_authority_is_a_host_position() -> None:
    template = URITemplate.of("//{host}/x{/name}")

    assert template.match("//acme/x/a") == {"host": "acme", "name": "a"}
    assert template.match("//evil@acme/x/a") is None
    assert template.match("//evil%40acme/x/a") is None
    assert template.match("//evil%23/x/a") is None
    with pytest.raises(ValueError):
        template.expand({"host": "a@b", "name": "a"})


def test_variable_scheme_does_not_hide_the_authority() -> None:
    template = URITemplate.of("{scheme}://{host}/x")

    assert template.match("https://acme/x") == {"scheme": "https", "host": "acme"}
    assert template.match("https://a%40b/x") is None
    assert template.match("https://a%23b/x") is None


def test_scheme_separator_within_a_path_or_query_is_not_an_authority() -> None:
    redirect = URITemplate.of("/redirect?u=http://{target}/x")
    path = URITemplate.of("/proxy/http://{target}")

    # not a host position - nothing there makes an `@` a user-info
    assert redirect.match("/redirect?u=http://a%40b/x") == {"target": "a@b"}
    assert path.match("/proxy/http://a%40b") == {"target": "a@b"}


def test_literal_authority_must_match_exactly() -> None:
    template = URITemplate.of("https://api.example.com/x{/id}")

    assert template.match("https://api.example.com/x/1") == {"id": "1"}
    assert template.match("https://api.example.com@evil.com/x/1") is None
    assert template.match("https://evil.com/https://api.example.com/x/1") is None
    assert template.match("https://api.example.com.evil.com/x/1") is None
    assert template.match("https://API.example.com/x/1") is None


def test_declared_query_parameters_only_and_first_value_wins() -> None:
    template = URITemplate.of("/a{?x}")

    assert template.match("/a?y=1&x=1&x=2") == {"x": "1"}
    assert template.match("/a?x=1&__proto__=2&x[]=3") == {"x": "1"}


def test_parameter_names_never_collide_with_undeclared_query_keys() -> None:
    template = URITemplate.of("/a/{id}{?page}")

    # `id` is a path variable - a query key of the same name is not a declared one
    assert template.match("/a/1?id=2&page=3") == {"id": "1", "page": "3"}


def test_template_caches_are_bounded() -> None:
    for index in range(template_module._CACHE_SIZE * 2):  # pyright: ignore[reportPrivateUsage]
        URITemplate.of(f"/items/{index}/{{id}}").match(f"/items/{index}/1")

    for cache in (
        template_module._parse,  # pyright: ignore[reportPrivateUsage]
        template_module._pattern,  # pyright: ignore[reportPrivateUsage]
    ):
        info = cache.cache_info()
        assert info.maxsize == template_module._CACHE_SIZE  # pyright: ignore[reportPrivateUsage]
        assert info.currsize <= info.maxsize


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (range(3), "0,1,2"),
        (deque(["a", "b"]), "a,b"),
        ((1, 2.5, True), "1,2.5,true"),
        (UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        (Decimal("1.50"), "1.50"),
        (PurePosixPath("a.txt"), "a.txt"),
        (3.5, "3.5"),
        (False, "false"),
    ],
)
def test_expand_supports_scalars_and_sequences_of_any_kind(value: object, expected: str) -> None:
    assert URITemplate.of("/a/{x}").expand({"x": value}) == f"/a/{expected}"
    assert URITemplate.of("/a{?x}").expand({"x": value}) == f"/a?x={expected}"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("%41", "/a#%41"),
        ("50%", "/a#50%25"),
        ("%zz", "/a#%25zz"),
        ("%4", "/a#%254"),
        ("a%41%", "/a#a%41%25"),
        (["%41", "%"], "/a#%41,%25"),
    ],
)
def test_fragment_keeps_valid_percent_encoded_triplets(value: object, expected: str) -> None:
    # RFC 6570, 3.2.1: a triplet passes as it is where reserved characters are allowed
    assert URITemplate.of("/a{#x}").expand({"x": value}) == expected
    # the other expansions encode the percent sign
    assert URITemplate.of("/a/{x}").expand({"x": "%41"}) == "/a/%2541"
    assert URITemplate.of("/a{?x}").expand({"x": "%41"}) == "/a?x=%2541"


def test_fragment_triplet_matches_back_decoded() -> None:
    template = URITemplate.of("/a{#x}")

    assert template.match(template.expand({"x": "%41"})) == {"x": "A"}


@pytest.mark.parametrize("value", ["%0a", "%00", "%0D%0A", "a\nb", "%ff", "%c0%af", ["ok", "%7f"]])
def test_expand_refuses_fragment_values_match_would_refuse(value: object) -> None:
    with pytest.raises(ValueError):
        URITemplate.of("/a{#x}").expand({"x": value})


@pytest.mark.parametrize("value", ["a\nb", "a\x00b", "a\rb", "a\x7fb", ["ok", "a\nb"]])
@pytest.mark.parametrize("template", ["/a{?x}", "/a{?x*}"])
def test_expand_refuses_control_characters_within_query_values(
    template: str,
    value: object,
) -> None:
    # quoting would encode them, matching refuses them - expansion has to as well
    with pytest.raises(ValueError):
        URITemplate.of(template).expand({"x": value})


@pytest.mark.parametrize(
    "template",
    [
        "/{/host}/x",
        "/a/{/b}",
        "//{/host}",
        "https://{host}/{/name}",
    ],
)
def test_empty_segments_are_rejected(template: str) -> None:
    # `/{/host}` would form a `//` authority which no host rule is applied to
    with pytest.raises(ValidationError):
        URITemplate.of(template)


@pytest.mark.parametrize(
    "template",
    [
        "/{a}{?q}{b}/end",
        "/{a}{?q}/end",
        "/{a}{?q}x",
        "/{a}{#f}x",
        "/{a}{#f}{b}",
        "/{a}{?q}{/b}",
        "/{a}{#f}{?q}",
        "/{a}{?q}{?r}",
        "/{a}{#f}{#g}",
    ],
)
def test_nothing_follows_a_query_or_a_fragment(template: str) -> None:
    # the optional expression would not separate what follows from what precedes it, and
    # overlapping captures take time polynomial in the length of the URI
    with pytest.raises(ValidationError):
        URITemplate.of(template)


@pytest.mark.parametrize("template", ["/{a}{?q}", "/{a}{#f}", "/{a}{?q}{#f}", "/a{/b}{?q}{#f}"])
def test_query_and_fragment_may_end_a_template(template: str) -> None:
    URITemplate.of(template)
