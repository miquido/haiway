from collections.abc import ItemsView, Iterable, Mapping, Sequence, Set
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from haiway.types import MISSING

__all__ = (
    "escape_controls",
    "format_log_message",
    "format_str",
)

# control characters allow forging additional log records, hiding written text
# and injecting terminal escape sequences, so they are never emitted verbatim.
# tab is kept - it cannot start a new record and it keeps output readable
_LINE_FEED: Final[int] = 0x0A
_CARRIAGE_RETURN: Final[int] = 0x0D
_CONTROL_ESCAPES: Final[Mapping[int, str]] = {
    **{
        code: f"\\x{code:02x}"
        for code in (*range(0x00, 0x09), *range(0x0B, 0x20), *range(0x7F, 0xA0))
    },
    _LINE_FEED: "\\n",
    _CARRIAGE_RETURN: "\\r",
}
_CONTROL_ESCAPES_KEEPING_NEWLINES: Final[Mapping[int, str]] = {
    code: escape for code, escape in _CONTROL_ESCAPES.items() if code != _LINE_FEED
}


def escape_controls(
    text: str,
    /,
    *,
    allow_newlines: bool = False,
) -> str:
    """
    Escape control characters which could tamper with rendered output.

    Parameters
    ----------
    text : str
        Text to escape.
    allow_newlines : bool, default=False
        When ``True``, line feeds are preserved - use it for text rendered
        within an already multiline structure. Carriage returns, terminal escape
        sequences and other control characters are escaped either way.

    Returns
    -------
    str
        Text with control characters replaced by their escaped representation.

    Notes
    -----
    Escaping keeps untrusted content from forging log records - a value
    containing a line feed would otherwise be written as an additional,
    seemingly independent line.
    """
    return text.translate(_CONTROL_ESCAPES_KEEPING_NEWLINES if allow_newlines else _CONTROL_ESCAPES)


def format_log_message(
    message: str,
    /,
    args: Sequence[Any] = (),
) -> str:
    """
    Compose a single line log message out of a format string and its arguments.

    Parameters
    ----------
    message : str
        Message, optionally containing ``%``-style placeholders.
    args : Sequence[Any], default=()
        Values substituted into the placeholders.

    Returns
    -------
    str
        Escaped, single line message with the arguments already substituted.

    Notes
    -----
    Interpolation happens here instead of within the logging module, so a
    ``%`` character coming from untrusted content cannot make the standard
    library drop the whole record. A malformed format string keeps the message
    and appends the arguments instead of failing.
    """
    if not args:
        return escape_controls(message)

    formatted: str
    try:
        formatted = message % args

    except TypeError, ValueError:
        # a mismatched format string must not lose the record
        formatted = f"{message} {args!r}"

    return escape_controls(formatted)


def format_str(  # noqa: PLR0911 PLR0912 C901
    value: Any,
    /,
    *,
    indent: int = 0,
) -> str:
    """
    Format any Python value into a readable string representation.

    Creates a human-readable string representation of complex data structures,
    with proper indentation and formatting for nested structures. This is especially
    useful for logging, debugging, and observability contexts.

    Parameters
    ----------
    value : Any
        The value to format as a string
    indent : int, default 0
        Left padding (in spaces) applied to the produced representation; used
        internally when formatting nested structures.

    Returns
    -------
    str
        A formatted string representation of the input value

    Notes
    -----
    - Strings are quoted, with multiline strings rendered as indented triple-quoted blocks
    - Control characters within strings are escaped, keeping line feeds of multiline blocks
    - Bytes-like values are rendered as ``<<<N bytes>>>``
    - Mappings are formatted with rendered keys and values
    - Sequences are formatted with positional indices
    - Objects with ``__dict__`` are rendered from their public attributes
    - Other objects fall back to ``str(obj)`` while preserving caller-managed indentation
    - ``MISSING`` values render as empty strings and are skipped by nested formatters
    - Nested structures maintain indentation recursively
    """
    if value is None:
        return "None"

    elif value is MISSING:
        return ""

    elif isinstance(value, str):
        # line feeds keep the readable block form, every other control character
        # is escaped so it can't inject terminal sequences or hide written text
        escaped_value: str = escape_controls(
            value,
            allow_newlines=True,
        )
        if "\n" in escaped_value:
            outer_indent = " " * indent
            inner_indent = " " * (indent + 2)
            indented_value = escaped_value.replace("\n", f"\n{inner_indent}")
            return f'{outer_indent}"""\n{inner_indent}{indented_value}\n{outer_indent}"""'

        else:
            return f'"{escaped_value}"'

    # bool is a subclass of int, so it is covered here as well - both render
    # through `str`, which is what the dedicated branch below used to do
    elif isinstance(value, int | float | complex):
        return str(value)

    elif isinstance(value, bytes | bytearray | memoryview):
        return f"<<<{len(value)} bytes>>>"  # pyright: ignore[reportUnknownArgumentType]

    elif isinstance(value, set | frozenset | Set):
        return _set_str(
            value,  # pyright: ignore[reportUnknownArgumentType]
            indent=indent,
        )

    elif isinstance(value, Mapping):
        return _mapping_str(
            value,  # pyright: ignore[reportUnknownArgumentType]
            indent=indent,
        )

    elif isinstance(value, Sequence):
        return _sequence_str(
            value,  # pyright: ignore[reportUnknownArgumentType]
            indent=indent,
        )

    elif isinstance(value, UUID):
        return str(value)

    elif isinstance(value, datetime):
        return value.isoformat()

    else:  # fallback to object
        return _object_str(
            value,
            indent=indent,
        )


def _attribute_str(
    *,
    key: str,
    value: str,
    indent: int,
) -> str:
    indent_str = " " * indent
    if "\n" in value:
        # Don't add extra indentation - value should already handle it
        return f"{indent_str}┝ {key}:\n{value}"

    else:
        return f"{indent_str}┝ {key}: {value}"


def _element_str(
    *,
    key: Any,
    value: str,
    indent: int,
) -> str:
    indent_str = " " * indent
    if "\n" in value:
        # Don't add extra indentation - value should already handle it
        return f"{indent_str}[{key}]:\n{value}"

    else:
        return f"{indent_str}[{key}]: {value}"


def _object_variables(
    other: object,
    /,
) -> Iterable[tuple[str, str | None, Any]] | None:
    # `State` and `Immutable` keep their values in slots, so the names come from
    # what the type declares rather than from an instance `__dict__`
    if fields := getattr(type(other), "__FIELDS__", ()):
        # an attribute annotated with Sensitive carries the redaction to render
        # instead of its value, so a secret cannot reach logs through formatting
        return (
            (field.name, field.redaction, getattr(other, field.name))
            for field in fields
            if hasattr(other, field.name)
        )

    if attributes := getattr(type(other), "__ATTRIBUTES__", ()):
        return ((name, None, getattr(other, name)) for name in attributes if hasattr(other, name))

    if hasattr(other, "__dict__"):
        return ((key, None, value) for key, value in vars(other).items())

    return None  # nothing describes it, it renders through `str` instead


def _object_str(
    other: object,
    /,
    *,
    indent: int,
) -> str:
    indent_str: str = " " * indent
    variables: Iterable[tuple[str, str | None, Any]] | None = _object_variables(other)
    if variables is None:
        # Preserve caller indentation across multiline string representations
        raw = str(other)
        lines = raw.splitlines(keepends=True)
        if not lines:
            return raw

        head, *tail = lines
        return head + "".join(f"{indent_str}{line}" for line in tail)

    header = f"{indent_str}┍━ {type(other).__name__}:"
    parts: list[str] = [header]
    for key, redaction, value in variables:
        if key.startswith("_"):
            continue  # skip private and dunder

        value_string: str
        if redaction is not None:
            # single line values are indented by the attribute renderer
            value_string = redaction

        else:
            value_string = format_str(
                value,
                indent=indent + 2,
            )

        if value_string:
            parts.append(
                _attribute_str(
                    key=key,
                    value=value_string,
                    indent=indent,
                )
            )

        else:
            continue  # skip empty elements

    return "\n".join(parts) + f"\n{indent_str}┕━"


def _mapping_str(
    mapping: Mapping[Any, Any],
    /,
    *,
    indent: int,
) -> str:
    items: ItemsView[Any, Any] = mapping.items()

    indent_str = " " * indent
    parts: list[str] = []
    for key, value in items:
        value_string: str = format_str(
            value,
            indent=indent + 2,
        )

        if value_string:
            parts.append(
                _element_str(
                    key=format_str(
                        key,
                        indent=indent + 2,
                    ),
                    value=value_string,
                    indent=indent + 2,
                )
            )

        else:
            continue  # skip empty items

    if parts:
        open_brace = "{\n" if indent == 0 else f"{indent_str}{{\n"
        close_brace = "\n}" if indent == 0 else f"\n{indent_str}}}"
        return open_brace + "\n".join(parts) + close_brace

    else:
        return "{}" if indent == 0 else f"{indent_str}{{}}"


def _set_str(
    set_value: Set[Any] | set[Any] | frozenset[Any],
    /,
    *,
    indent: int,
) -> str:
    indent_str: str = " " * indent
    element_indent_str: str = " " * (indent + 2)
    parts: list[str] = []
    for element in set_value:
        element_string: str = format_str(
            element,
            indent=indent + 2,
        )

        if element_string:
            parts.append(f"{element_indent_str}{element_string}")

        else:
            continue  # skip empty elements

    if parts:
        open_brace: str = f"{indent_str}{{\n"
        close_brace: str = f"\n{indent_str}}}"
        return open_brace + ",\n".join(parts) + close_brace

    else:
        return f"{indent_str}{{}}"


def _sequence_str(
    sequence: Sequence[Any],
    /,
    *,
    indent: int,
) -> str:
    indent_str: str = " " * indent
    parts: list[str] = []
    for idx, element in enumerate(sequence):
        element_string: str = format_str(
            element,
            indent=indent + 2,
        )

        if element_string:
            parts.append(
                _element_str(
                    key=idx,
                    value=element_string,
                    indent=indent + 2,
                )
            )

        else:
            continue  # skip empty elements

    if parts:
        open_bracket: str = f"{indent_str}[\n"
        close_bracket: str = f"\n{indent_str}]"
        return open_bracket + "\n".join(parts) + close_bracket

    else:
        return f"{indent_str}[]"
