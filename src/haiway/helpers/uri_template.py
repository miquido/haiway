import re
from collections.abc import Mapping, Sequence
from functools import cache
from re import Pattern
from typing import Annotated, Any, NamedTuple, Self, cast, final
from urllib.parse import parse_qs, quote, unquote, urlencode

from haiway.attributes import State, Verifier
from haiway.types import Map

__all__ = ("URITemplate",)

_NAME: Pattern[str] = re.compile(r"[A-Za-z0-9_]+")
_EXPRESSION: Pattern[str] = re.compile(r"(\{[^{}]*\})")
_DELIMITER: Pattern[str] = re.compile(r"[/?#]")
_SEGMENT: str = r"[^/?#]+"
# a variable within the authority stands for a host label, never for the user-info
# delimiter - allowing `@` there would let `https://evil.com@acme.example.com/lib` match
# `https://{tenant}.example.com{/repo}` and hand over `evil.com@acme` as the host label
_AUTHORITY_SEGMENT: str = r"[^/?#@]+"


class _Literal(NamedTuple):
    text: str


class _Variable(NamedTuple):
    name: str
    authority: bool


class _Segment(NamedTuple):
    name: str


class _Query(NamedTuple):
    names: tuple[str, ...]


class _Fragment(NamedTuple):
    name: str


type _Part = _Literal | _Variable | _Segment | _Query | _Fragment


def _verified(value: str) -> str:
    _parse(value)
    return value


@final
class URITemplate(State):
    """
    Immutable RFC 6570 (level 3 subset) URI template.

    Supported expressions are ``{name}`` (a single path segment or host label),
    ``{/name}`` (a slash-prefixed path segment), ``{?a,b}`` (query parameters) and
    ``{#name}`` (a fragment). The template can both expand into a URI and match an
    existing URI back into its parameters, and every value extracted from a URI is held to
    the segment boundary the template declared, so that decoding can never cross it.

    Parameters
    ----------
    template : str
        Template text, verified when the template is created.

    Raises
    ------
    ValidationError
        If the template contains unbalanced braces, an unsupported or malformed
        expression, a repeated variable or a query expression following a fragment.

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

        Path segments and fragments are percent-encoded. Sequences expand as comma
        separated path values and as repeated query keys. Query and fragment expressions
        are omitted when no value is provided, other variables are required.
        A path or host value decoding to a path separator, a relative segment (``.``,
        ``..``) or, within a host, user-info is refused - the same values ``match`` refuses.

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
            If a required variable has no value or an empty one.
        """
        expanded: list[str] = []
        queried: bool = False
        for part in _parse(self.template):
            match part:
                case _Literal(text):
                    expanded.append(text)
                    queried = queried or "?" in text

                case _Variable(name, authority):
                    expanded.append(_required(values, name, authority=authority))

                case _Segment(name):
                    expanded.append("/" + _required(values, name, authority=False))

                case _Query(names):
                    items: list[tuple[str, str]] = _query_items(values, names)
                    if items:
                        prefix: str = "&" if queried else "?"
                        queried = True
                        expanded.append(prefix + urlencode(items))

                case _Fragment(name):
                    value: Any = values.get(name)
                    if value is not None and (encoded := _encoded(value)):
                        expanded.append("#" + encoded)

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
        each is used. A URI whose decoded path or host parameter contains a path
        separator, a relative segment (``.``, ``..``) or user-info does not match.

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

        case _Query(names):
            return names

        case _Variable(name, _) | _Segment(name) | _Fragment(name):
            return (name,)


def _bound(
    part: _Part,
    captured: str,
    /,
) -> Mapping[str, str] | None:
    match part:
        case _Variable(name, authority):
            decoded: str | None = _decoded(captured, authority=authority)

        case _Segment(name):
            decoded = _decoded(captured, authority=False)

        case _Query(names):
            return {
                key: found[0] for key, found in parse_qs(captured).items() if found and key in names
            }

        case _Fragment(name):
            return {name: unquote(captured)}

        case _Literal():
            return {}

    return None if decoded is None else {name: decoded}


def _expression(
    token: str,
    /,
    *,
    authority: bool,
    template: str,
) -> _Part:
    operator: str = token[1] if token[1:2] in ("/", "?", "#") else ""
    names: tuple[str, ...] = tuple(
        name.strip() for name in token[1 + len(operator) : -1].split(",")
    )
    if any(_NAME.fullmatch(name) is None for name in names) or (
        operator != "?" and len(names) != 1
    ):
        raise ValueError(f"Unsupported expression {token} in URI template: {template}")

    match operator:
        case "/":
            return _Segment(names[0])

        case "?":
            return _Query(names)

        case "#":
            return _Fragment(names[0])

        case _:
            return _Variable(names[0], authority)


def _authority_after(
    literal: str,
    /,
    *,
    authority: bool,
    pending: bool,
) -> tuple[bool, bool]:
    # the authority begins after the scheme and ends at the first delimiter, variable
    # standing within it are held to narrower rules than the path ones
    if pending and "://" in literal:
        return _DELIMITER.search(literal.split("://", 1)[1]) is None, False

    return authority and _DELIMITER.search(literal) is None, pending


@cache
def _parse(template: str) -> tuple[_Part, ...]:
    parts: list[_Part] = []
    seen: set[str] = set()
    pending: bool = "://" in template
    authority: bool = False
    ended: bool = False  # a fragment precedes, literal or not
    for index, token in enumerate(_EXPRESSION.split(template)):
        if index % 2 == 0:
            if "{" in token or "}" in token:
                raise ValueError(f"Unbalanced braces in URI template: {template}")

            parts.extend(_Literal(token) for _ in (token,) if token)
            authority, pending = _authority_after(token, authority=authority, pending=pending)
            ended = ended or "#" in token
            continue

        part: _Part = _expression(token, authority=authority, template=template)
        if isinstance(part, _Query | _Fragment) and (
            ended or (isinstance(part, _Query) and any(isinstance(it, _Query) for it in parts))
        ):
            raise ValueError(f"Misplaced expression {token} in URI template: {template}")

        if not seen.isdisjoint(names := _names(part)):
            raise ValueError(f"Repeated variable in URI template: {template}")

        seen.update(names)
        ended = ended or isinstance(part, _Fragment)
        authority = authority and isinstance(part, _Variable)
        parts.append(part)

    return tuple(parts)


@cache
def _variables(template: str) -> tuple[str, ...]:
    return tuple(name for part in _parse(template) for name in _names(part))


@cache
def _captured(template: str) -> tuple[_Part, ...]:
    return tuple(part for part in _parse(template) if not isinstance(part, _Literal))


@cache
def _pattern(template: str) -> Pattern[str]:
    tokens: list[str] = []
    queried: bool = False
    for part in _parse(template):
        match part:
            case _Literal(text):
                tokens.append(re.escape(text))
                queried = queried or "?" in text

            case _Variable(_, authority):
                tokens.append(f"({_AUTHORITY_SEGMENT if authority else _SEGMENT})")

            case _Segment():
                tokens.append(f"/({_SEGMENT})")

            case _Query():
                # expansion continues a literal query with `&`, the match has to as well
                tokens.append(r"(?:&([^#]*))?" if queried else r"(?:\?([^#]*))?")

            case _Fragment():
                tokens.append(r"(?:#(.*))?")

    return re.compile("".join(tokens), re.DOTALL)


def _unsafe(
    value: str,
    /,
    *,
    authority: bool,
) -> bool:
    return "/" in value or "\\" in value or value in (".", "..") or (authority and "@" in value)


def _decoded(
    value: str,
    /,
    *,
    authority: bool,
) -> str | None:
    # the pattern verified the raw URI, where every parameter spans exactly one segment.
    # Decoding happens afterwards, so a capture like `..%2Fsecret` would otherwise cross
    # the very boundary already verified
    decoded: str = unquote(value)
    return None if _unsafe(decoded, authority=authority) else decoded


def _items(value: Any) -> Sequence[Any] | None:
    # the common scalar and builtin sequence types are decided without the abstract base
    if isinstance(value, (str, bytes, int, float)):
        return None

    if isinstance(value, (list, tuple)):
        return cast(Sequence[Any], value)

    if isinstance(value, Sequence):
        return cast(Sequence[Any], value)

    return None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


def _encoded(value: Any) -> str:
    if (items := _items(value)) is not None:
        return ",".join(quote(_text(item), safe="") for item in items)

    return quote(_text(value), safe="")


def _required(
    values: Mapping[str, Any],
    name: str,
    *,
    authority: bool,
) -> str:
    value: Any = values.get(name)
    if value is None:
        raise ValueError(f"Missing value of URI template variable '{name}'")

    elements: Sequence[Any] | None = _items(value)
    texts: tuple[str, ...] = (
        (_text(value),) if elements is None else tuple(_text(item) for item in elements)
    )
    # the very rule matching holds a decoded value to, so that what expands also matches
    for text in texts:
        if _unsafe(text, authority=authority):
            raise ValueError(f"Unsafe value of URI template variable '{name}'")

    if not (encoded := ",".join(quote(text, safe="") for text in texts)):
        raise ValueError(f"Empty value of URI template variable '{name}'")

    return encoded


def _query_items(
    values: Mapping[str, Any],
    names: tuple[str, ...],
) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for name in names:
        value: Any = values.get(name)
        if value is None:
            continue

        if (elements := _items(value)) is not None:
            items.extend((name, _text(item)) for item in elements if item is not None)

        elif (text := _text(value)) != "":
            items.append((name, text))

    return items
