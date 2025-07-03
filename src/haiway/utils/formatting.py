from collections.abc import ItemsView, Mapping, Sequence
from typing import Any

from haiway.types.missing import MISSING

__all__ = ("format_str",)


def format_str(  # noqa: PLR0911
    value: Any,
    /,
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

    Returns
    -------
    str
        A formatted string representation of the input value

    Notes
    -----
    - Strings are quoted, with multi-line strings using triple quotes
    - Bytes are prefixed with 'b' and quoted
    - Mappings (like dictionaries) are formatted with keys and values
    - Sequences (like lists) are formatted with indices and values
    - Objects are formatted with their attribute names and values
    - MISSING values are converted to empty strings
    - Nested structures maintain proper indentation
    """
    # check for string
    if isinstance(value, str):
        if "\n" in value:
            return f'"""\n{value.replace("\n", "\n  ")}\n"""'

        else:
            return f'"{value}"'

    # check for bytes
    elif isinstance(value, bytes):
        return f"b'{value!r}'"

    # try unpack mapping
    elif isinstance(value, Mapping):
        return _mapping_str(value)

    # try unpack sequence
    elif isinstance(value, Sequence):
        return _sequence_str(value)

    elif value is MISSING:
        return ""

    else:  # fallback to object
        return _object_str(value)


def _attribute_str(
    *,
    key: str,
    value: str,
) -> str:
    if "\n" in value:
        formatted_value: str = value.replace("\n", "\n|  ")
        return f"┝ {key}:\n{formatted_value}"

    else:
        return f"┝ {key}: {value}"


def _element_str(
    *,
    key: Any,
    value: Any,
) -> str:
    if "\n" in value:
        formatted_value: str = value.replace("\n", "\n  ")
        return f"[{key}]:\n{formatted_value}"

    else:
        return f"[{key}]: {value}"


def _object_str(
    other: object,
    /,
) -> str:
    if not hasattr(other, "__dict__"):
        return str(other)

    variables: ItemsView[str, Any] = vars(other).items()

    parts: list[str] = [f"┍━ {type(other).__name__}:"]
    for key, value in variables:
        if key.startswith("_"):
            continue  # skip private and dunder

        value_string: str = format_str(value)

        if value_string:
            parts.append(
                _attribute_str(
                    key=key,
                    value=value_string,
                )
            )

        else:
            continue  # skip empty elements

    if parts:
        return "\n".join(parts) + "\n┕━"

    else:
        return ""


def _mapping_str(
    mapping: Mapping[Any, Any],
    /,
) -> str:
    items: ItemsView[Any, Any] = mapping.items()

    parts: list[str] = []
    for key, value in items:
        value_string: str = format_str(value)

        if value_string:
            parts.append(
                _element_str(
                    key=key,
                    value=value_string,
                )
            )

        else:
            continue  # skip empty items

    if parts:
        return "{\n" + "\n".join(parts) + "\n}"

    else:
        return "{}"


def _sequence_str(
    sequence: Sequence[Any],
    /,
) -> str:
    parts: list[str] = []
    for idx, element in enumerate(sequence):
        element_string: str = format_str(element)

        if element_string:
            parts.append(
                _element_str(
                    key=idx,
                    value=element_string,
                )
            )

        else:
            continue  # skip empty elements

    if parts:
        return "[\n" + "\n".join(parts) + "\n]"

    else:
        return "[]"
