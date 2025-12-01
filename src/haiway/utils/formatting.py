from collections.abc import ItemsView, Mapping, Sequence, Set
from datetime import datetime
from typing import Any
from uuid import UUID

from haiway.types.missing import MISSING

__all__ = ("format_str",)


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
    - Strings are quoted, with multi-line strings using triple quotes
    - Bytes are noted with its size
    - Mappings (like dictionaries) are formatted with keys and values
    - Sequences (like lists) are formatted with indices and values
    - Objects with ``__dict__`` are formatted with their attribute names and values;
      other objects fall back to ``str(obj)`` while preserving caller-managed
      indentation
    - MISSING values are converted to empty strings
    - Nested structures maintain proper indentation
    """
    if value is None:
        return "None"

    elif value is MISSING:
        return ""

    elif isinstance(value, str):
        if "\n" in value:
            outer_indent = " " * indent
            inner_indent = " " * (indent + 2)
            indented_value = value.replace("\n", f"\n{inner_indent}")
            return f'{outer_indent}"""\n{inner_indent}{indented_value}\n{outer_indent}"""'

        else:
            return f'"{value}"'

    elif isinstance(value, int | float | complex):
        return str(value)

    elif isinstance(value, bool):
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


def _object_str(
    other: object,
    /,
    *,
    indent: int,
) -> str:
    indent_str: str = " " * indent
    if not hasattr(other, "__dict__"):
        # Preserve caller indentation across multiline string representations
        raw = str(other)
        lines = raw.splitlines(keepends=True)
        if not lines:
            return raw

        head, *tail = lines
        return head + "".join(f"{indent_str}{line}" for line in tail)

    variables: ItemsView[str, Any] = vars(other).items()
    header = f"{indent_str}┍━ {type(other).__name__}:"
    parts: list[str] = [header]
    for key, value in variables:
        if key.startswith("_"):
            continue  # skip private and dunder

        value_string: str = format_str(
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
