import re
from collections.abc import Mapping, Sequence, Set
from functools import lru_cache
from re import Pattern
from typing import Annotated, Any, Literal, NamedTuple, Self, cast, final
from unicodedata import normalize
from urllib.parse import parse_qs, quote, unquote

from haiway.attributes import State, Verifier
from haiway.types import Map

__all__ = ("URITemplate",)

# RFC 6570 varname without the percent-encoded form: dot separated runs of word characters
_NAME: Pattern[str] = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_EXPRESSION: Pattern[str] = re.compile(r"(\{[^{}]*\})")
_DELIMITER: Pattern[str] = re.compile(r"[/?#]")
_CONTROL: Pattern[str] = re.compile(r"[\x00-\x1f\x7f]")
_SEGMENT: str = r"[^/?#]+"
# a variable within the authority stands for a host label, never for the user-info
# delimiter - allowing `@` there would let `https://evil.com@acme.example.com/lib` match
# `https://{tenant}.example.com{/repo}` and hand over `evil.com@acme` as the host label
_AUTHORITY_DELIMITERS: str = "/?#@"
_AUTHORITY_SEGMENT: str = f"[^{_AUTHORITY_DELIMITERS}]+"
# within a literal query a value ends where the next parameter begins, or - as the query
# is expanded after it with `&` - the capture would swallow the parameters that follow
_QUERY_SEGMENT: str = r"[^/?#&]+"
_AUTHORITY_UNSAFE: Pattern[str] = re.compile(rf"[\s{re.escape(_AUTHORITY_DELIMITERS)}]")
# RFC 6570 reserved set (gen-delims and sub-delims) a fragment expansion leaves as it is
_RESERVED: str = ":/?#[]@!$&'()*+,;="
_TRIPLET: Pattern[str] = re.compile(r"(%[0-9A-Fa-f]{2})")
_CACHE_SIZE: int = 1024
_SEGMENTS: Mapping[str, str] = {
    "path": _SEGMENT,
    "authority": _AUTHORITY_SEGMENT,
    "query": _QUERY_SEGMENT,
}


class _Literal(NamedTuple):
    text: str


type _Region = Literal["path", "authority", "query"]


class _Variable(NamedTuple):
    name: str
    region: _Region


class _Segment(NamedTuple):
    name: str


class _QueryVariable(NamedTuple):
    name: str
    explode: bool


class _Query(NamedTuple):
    variables: tuple[_QueryVariable, ...]


class _Fragment(NamedTuple):
    name: str


type _Captured = _Variable | _Segment | _Query | _Fragment
type _Part = _Literal | _Captured


def _verified(value: str) -> str:
    _parse(value)
    return value


@final
class URITemplate(State):
    """
    Immutable URI template following RFC 6570, a subset of its levels 1-4.

    Supported expressions are ``{name}`` (a path segment or host label), ``{/name}`` (a
    slash-prefixed path segment), ``{?a,b*}`` (query parameters, ``*`` explodes a list into
    repeated keys) and ``{#name}`` (a fragment). Expansion follows the RFC: unreserved
    characters only are left as they are, a list expands comma separated, a query value
    keeps its empty form (``x=``), an undefined value (``None``) is omitted. The reserved,
    label, matrix and continuation operators (``+ . ; &``), multiple names within one
    expression other than a query, prefix modifiers and mappings are not supported.

    The template can also match an existing URI back into its parameters, which an RFC
    template does not define. There, every value is held to the segment boundary the
    template declared, so that decoding can never cross it, which makes the template safe
    to guard lookups with. For the same reason a template has at most one variable per
    segment - several of them within one would be ambiguous and expensive to match - and
    path and host variables are required and never expand to an empty, unsafe value.

    Parameters
    ----------
    template : str
        Template text, verified when the template is created.

    Raises
    ------
    ValidationError
        If the template contains unbalanced braces, an unsupported or malformed
        expression, a repeated variable, several variables within one segment, an empty
        segment (``/{/name}``) or a query or fragment which is not the end of the template.

    Examples
    --------
    >>> template = URITemplate.of("https://{tenant}.example.com/files{/name}{?page}")
    >>> template.expand({"tenant": "acme", "name": "a b.txt", "page": 2})
    'https://acme.example.com/files/a%20b.txt?page=2'
    >>> template.match("https://acme.example.com/files/a%20b.txt?page=2")
    Map({'tenant': 'acme', 'name': 'a b.txt', 'page': '2'})
    """

    template: Annotated[str, Verifier(_verified)]

    @classmethod
    def of(
        cls,
        template: str,
        /,
    ) -> Self:
        """
        Create a template from its text.

        Parameters
        ----------
        template : str
            Template text.

        Returns
        -------
        Self
            Template ready to expand and match.

        Raises
        ------
        ValidationError
            If the template is malformed.
        """
        return cls(template=template)

    @property
    def variables(self) -> Sequence[str]:
        """Variable names in the order of their appearance within the template."""
        return _variables(self.template)

    def expand(
        self,
        values: Mapping[str, Any],
        /,
    ) -> str:
        """
        Expand the template into a URI.

        Values are percent-encoded, only unreserved characters are left as they are,
        except in a fragment which keeps the reserved ones as well. A sequence expands
        comma separated, or as repeated keys within a query when exploded. A fragment also
        passes a valid percent-encoded triplet (``%41``) as it is. Query and
        fragment expressions are omitted when no value is provided, other variables are
        required. A value containing a control character, a path or host value decoding to a
        path separator or a relative segment (``.``, ``..``) and a host one decoding to a
        delimiter or whitespace is refused - the same values ``match`` refuses.

        Parameters
        ----------
        values : Mapping[str, Any]
            Values of variables, ``None`` stands for a missing one.

        Returns
        -------
        str
            Expanded URI.

        Raises
        ------
        ValueError
            If a required variable has no value, an empty one or an unsafe one.
        TypeError
            If a value is of an unsupported type - bytes, mapping, set or a nested sequence.
        """
        expanded: list[str] = []
        queried: bool = False
        for part in _parse(self.template):
            match part:
                case _Literal(text):
                    expanded.append(text)
                    queried = queried or "?" in text

                case _Variable(name, region):
                    expanded.append(_required(values, name, authority=region == "authority"))

                case _Segment(name):
                    expanded.append("/" + _required(values, name, authority=False))

                case _Query(variables):
                    if pairs := _query_pairs(values, variables):
                        expanded.append(("&" if queried else "?") + "&".join(pairs))
                        queried = True

                case fragment:
                    if (joined := _fragment(values.get(fragment.name), fragment.name)) is not None:
                        expanded.append("#" + joined)

        return "".join(expanded)

    def matches(
        self,
        uri: str,
        /,
    ) -> bool:
        """
        Check if the URI is an expansion of this template.

        Parameters
        ----------
        uri : str
            URI to verify.

        Returns
        -------
        bool
            True if the URI matches and all of its parameters are safe to use.
        """
        return self.match(uri) is not None

    def match(
        self,
        uri: str,
        /,
    ) -> Map[str, str] | None:
        """
        Match the URI against this template and extract its parameters.

        Only query parameters declared by the template are extracted, the first value of
        each is used and an empty one is kept. A URI whose decoded parameter contains a
        control character or is not valid UTF-8, whose decoded path or host parameter
        contains a path separator or a relative segment (``.``, ``..``), or whose decoded
        host parameter contains a delimiter, whitespace or a character normalizing to one,
        does not match.

        Parameters
        ----------
        uri : str
            URI to match.

        Returns
        -------
        Map[str, str] | None
            Decoded parameters or None if the URI does not match.
        """
        matched: re.Match[str] | None = _pattern(self.template).fullmatch(uri)
        if matched is None:
            return None

        values: dict[str, str] = {}
        for part, captured in zip(_captured(self.template), matched.groups(), strict=True):
            if captured is None:
                continue

            if (bound := _bound(part, captured)) is None:
                return None

            values.update(bound)

        return Map(values)

    def __str__(self) -> str:
        return self.template


def _names(part: _Part) -> tuple[str, ...]:
    match part:
        case _Literal():
            return ()

        case _Query(variables):
            return tuple(variable.name for variable in variables)

        case named:
            return (named.name,)


def _bound(
    part: _Captured,
    captured: str,
    /,
) -> Mapping[str, str] | None:
    try:
        match part:
            case _Variable(name, region):
                decoded: str | None = _decoded(captured, authority=region == "authority")

            case _Segment(name):
                decoded = _decoded(captured, authority=False)

            case _Query(variables):
                names: tuple[str, ...] = tuple(variable.name for variable in variables)
                found: Mapping[str, str] = {
                    key: elements[0]
                    for key, elements in parse_qs(
                        captured,
                        keep_blank_values=True,
                        errors="strict",
                    ).items()
                    if elements and key in names
                }
                return None if any(_CONTROL.search(value) for value in found.values()) else found

            case fragment:
                decoded = unquote(captured, errors="strict")
                if _CONTROL.search(decoded):
                    return None

                name = fragment.name

    except UnicodeDecodeError:
        # not valid UTF-8 - an overlong form like `%c0%af` is how a slash gets smuggled
        return None

    return None if decoded is None else {name: decoded}


def _expression(
    token: str,
    /,
    *,
    region: _Region,
    template: str,
) -> _Part:
    operator: str = token[1] if token[1:2] in ("/", "?", "#") else ""
    names: tuple[str, ...] = tuple(token[1 + len(operator) : -1].split(","))
    if operator == "?":
        variables: tuple[_QueryVariable, ...] = tuple(
            _QueryVariable(name.removesuffix("*"), name.endswith("*")) for name in names
        )
        if all(_NAME.fullmatch(variable.name) for variable in variables):
            return _Query(variables)

    elif len(names) == 1 and _NAME.fullmatch(names[0]):
        match operator:
            case "/":
                return _Segment(names[0])

            case "#":
                return _Fragment(names[0])

            case _:
                return _Variable(names[0], region)

    raise ValueError(f"Unsupported expression {token} in URI template: {template}")


def _authority_start(
    literal: str,
    /,
    *,
    first: bool,
) -> int | None:
    # the authority follows a scheme - or a leading `//` - and only while no delimiter
    # precedes, otherwise `://` is just a part of a path or of a query
    if (index := literal.find("://")) != -1 and _DELIMITER.search(literal[:index]) is None:
        return index + 3

    if first and literal.startswith("//"):
        return 2

    return None


def _authority_after(
    literal: str,
    /,
    *,
    authority: bool,
    pending: bool,
    first: bool,
) -> tuple[bool, bool]:
    # the authority ends at the first delimiter, variable standing within it are held to
    # narrower rules than the path ones
    if pending and (start := _authority_start(literal, first=first)) is not None:
        return _DELIMITER.search(literal[start:]) is None, False

    delimited: bool = _DELIMITER.search(literal) is not None
    return authority and not delimited, pending and not delimited


def _tail_after(part: _Part, /) -> str:
    match part:
        case _Query():
            return "query"

        case _Fragment():
            return "fragment"

        case _:
            return ""


def _verify_placement(
    part: _Part,
    parts: Sequence[_Part],
    /,
    *,
    tail: str,
    ended: bool,
    region: _Region,
    template: str,
) -> None:
    # a query and a fragment end a URI, nothing but a fragment may follow a query - what
    # followed would be matched along with them and overlap the captures before
    if (
        tail == "fragment"
        or (tail == "query" and not isinstance(part, _Fragment))
        or (isinstance(part, _Query | _Fragment) and ended)
    ):
        raise ValueError(f"Misplaced expression {part} in URI template: {template}")

    # there are no segments within a query - the expression would capture past its parameters
    if isinstance(part, _Segment) and region == "query":
        raise ValueError(f"Slash expression within a query in URI template: {template}")

    # a slash expression following a slash makes an empty segment - `/{/host}` would
    # begin a `//` authority that no host rule is applied to
    if (
        isinstance(part, _Segment)
        and parts
        and isinstance(parts[-1], _Literal)
        and parts[-1].text.endswith("/")
    ):
        raise ValueError(f"Empty segment before {part} in URI template: {template}")


@lru_cache(maxsize=_CACHE_SIZE)
def _parse(template: str) -> tuple[_Part, ...]:
    parts: list[_Part] = []
    seen: set[str] = set()
    pending: bool = True  # no delimiter yet, the authority may still begin
    authority: bool = False
    queried: bool = False  # within a literal query
    ended: bool = False  # a fragment precedes within a literal
    tail: str = ""  # "query" or "fragment" once one of them has been expanded
    segmented: bool = False  # a variable within the current segment
    for index, token in enumerate(_EXPRESSION.split(template)):
        if index % 2 == 0:
            if "{" in token or "}" in token:
                raise ValueError(f"Unbalanced braces in URI template: {template}")

            if token and tail:
                raise ValueError(f"Text after a query or a fragment in URI template: {template}")

            parts.extend(_Literal(token) for _ in (token,) if token)
            authority, pending = _authority_after(
                token,
                authority=authority,
                pending=pending,
                first=index == 0,
            )
            ended = ended or "#" in token
            # the last of `?` and `#` decides where a literal leaves off
            queried = token.rfind("?") > token.rfind("#") or (queried and "#" not in token)
            segmented = segmented and _DELIMITER.search(token) is None
            continue

        region: _Region = "authority" if authority else "query" if queried else "path"
        part: _Part = _expression(token, region=region, template=template)
        _verify_placement(part, parts, tail=tail, ended=ended, region=region, template=template)

        if not seen.isdisjoint(names := _names(part)):
            raise ValueError(f"Repeated variable in URI template: {template}")

        # a segment holding several variables matches in many ways, which makes matching
        # a URI of an attacker polynomial in its length - one variable per segment is linear
        if isinstance(part, _Segment):
            segmented = False

        if isinstance(part, _Variable | _Segment):
            if segmented:
                raise ValueError(f"Several variables within a segment of URI template: {template}")

            segmented = True

        else:
            segmented = False

        seen.update(names)
        tail = _tail_after(part)
        authority = authority and isinstance(part, _Variable)
        pending = pending and isinstance(part, _Variable)
        parts.append(part)

    return tuple(parts)


@lru_cache(maxsize=_CACHE_SIZE)
def _variables(template: str) -> tuple[str, ...]:
    return tuple(name for part in _parse(template) for name in _names(part))


@lru_cache(maxsize=_CACHE_SIZE)
def _captured(template: str) -> tuple[_Captured, ...]:
    return tuple(part for part in _parse(template) if not isinstance(part, _Literal))


@lru_cache(maxsize=_CACHE_SIZE)
def _pattern(template: str) -> Pattern[str]:
    tokens: list[str] = []
    queried: bool = False
    for part in _parse(template):
        match part:
            case _Literal(text):
                tokens.append(re.escape(text))
                queried = queried or "?" in text

            case _Variable(_, region):
                tokens.append(f"({_SEGMENTS[region]})")

            case _Segment():
                tokens.append(f"/({_SEGMENT})")

            case _Query():
                # expansion continues a literal query with `&`, the match has to as well
                tokens.append(r"(?:&([^#]*))?" if queried else r"(?:\?([^#]*))?")

            case _:
                tokens.append(r"(?:#(.*))?")

    return re.compile("".join(tokens), re.DOTALL)


def _unsafe(
    value: str,
    /,
    *,
    authority: bool,
) -> bool:
    if "/" in value or "\\" in value or value in (".", "..") or _CONTROL.search(value):
        return True

    # a host label holds the same delimiters the raw capture excludes - decoding must not
    # reintroduce them, the matched URI never addressed them, nor may a character a URL
    # parser normalizes into one (`%EF%BC%8F` is a fullwidth slash)
    return authority and (
        _AUTHORITY_UNSAFE.search(value) is not None
        or _AUTHORITY_UNSAFE.search(normalize("NFKC", value)) is not None
    )


def _decoded(
    value: str,
    /,
    *,
    authority: bool,
) -> str | None:
    # the pattern verified the raw URI, where every parameter spans exactly one segment.
    # Decoding happens afterwards, so a capture like `..%2Fsecret` would otherwise cross
    # the very boundary already verified
    decoded: str = unquote(value, errors="strict")
    return None if _unsafe(decoded, authority=authority) else decoded


def _items(value: Any) -> Sequence[Any] | None:
    # the common scalar and builtin sequence types are decided without the abstract base
    if isinstance(value, (str, int, float)):
        return None

    if isinstance(value, (list, tuple)):
        return cast(Sequence[Any], value)

    if isinstance(value, (bytes, bytearray)):
        raise TypeError("URI template values can't be bytes")

    if isinstance(value, (Mapping, Set)):
        raise TypeError("URI template values can't be mappings or sets")

    if isinstance(value, Sequence):
        return cast(Sequence[Any], value)

    return None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, bool):
        return "true" if value else "false"

    if _items(value) is not None:
        raise TypeError("URI template values can't be nested sequences")

    return str(value)


def _texts(value: Any) -> tuple[str, ...]:
    # a sequence gives its defined elements, a scalar itself
    if (elements := _items(value)) is None:
        return (_text(value),)

    return tuple(_text(element) for element in elements if element is not None)


def _fragment(
    value: Any,
    name: str,
    /,
) -> str | None:
    # None stands for an undefined value - missing or an empty sequence
    if value is None or not (texts := _texts(value)):
        return None

    # a fragment keeps the reserved characters and, as RFC 6570 allows there, a valid
    # percent-encoded triplet; any other percent sign is encoded
    encoded: str = ",".join(
        "".join(
            part if index % 2 else quote(part, safe=_RESERVED)
            for index, part in enumerate(_TRIPLET.split(text))
        )
        for text in texts
    )
    # a triplet passes as it is, so what it decodes to has to be verified as matching does
    try:
        if _CONTROL.search(unquote(encoded, errors="strict")):
            raise ValueError(f"Unsafe value of URI template variable '{name}'")

    except UnicodeDecodeError as exc:
        raise ValueError(f"Unsafe value of URI template variable '{name}'") from exc

    return encoded


def _required(
    values: Mapping[str, Any],
    name: str,
    *,
    authority: bool,
) -> str:
    value: Any = values.get(name)
    if value is None or not (texts := _texts(value)):
        raise ValueError(f"Missing value of URI template variable '{name}'")

    # the very rule matching holds a decoded value to, so that what expands also matches
    for text in texts:
        if _unsafe(text, authority=authority):
            raise ValueError(f"Unsafe value of URI template variable '{name}'")

    if not (encoded := ",".join(quote(text, safe="") for text in texts)):
        raise ValueError(f"Empty value of URI template variable '{name}'")

    return encoded


def _query_pairs(
    values: Mapping[str, Any],
    variables: tuple[_QueryVariable, ...],
) -> list[str]:
    pairs: list[str] = []
    for name, explode in variables:
        value: Any = values.get(name)
        if value is None or not (texts := _texts(value)):
            continue

        if any(_CONTROL.search(text) for text in texts):
            raise ValueError(f"Unsafe value of URI template variable '{name}'")

        if explode:
            pairs.extend(f"{name}={quote(text, safe='')}" for text in texts)

        else:
            pairs.append(f"{name}={','.join(quote(text, safe='') for text in texts)}")

    return pairs
