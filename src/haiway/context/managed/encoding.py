import json
from typing import Any, cast

from haiway.attributes import State
from haiway.types.strict_json import finite_float, refused_constant

__all__ = (
    "encode_task_argument",
    "encode_task_result",
    "require_json_object_or_null",
)


def _decoded_json(text: str) -> Any:
    return json.loads(
        text,
        parse_constant=refused_constant,
        parse_float=finite_float,
    )


def _reject_nul(decoded: Any) -> None:
    # checks the decoded strings, not the text, so the six characters backslash-u-0-0-0-0
    # written as `"\\u0000"` stay valid
    pending: list[Any] = [decoded]
    while pending:
        element: Any = pending.pop()
        if isinstance(element, str):
            if "\x00" in element:
                raise ValueError("A string holding U+0000 is not accepted")

        elif isinstance(element, dict):
            for key, value in cast(dict[Any, Any], element).items():
                pending.append(key)
                pending.append(value)

        elif isinstance(element, list):
            pending.extend(cast(list[Any], element))


def encode_task_argument(
    argument: State,
    /,
    *,
    decoding: type[State],
) -> str:
    text: str = argument.to_json()
    # the argument has to survive the round trip the task depends on
    decoding.from_json(text)
    _reject_nul(_decoded_json(text))
    return text


def encode_task_result(
    value: State | None,
    /,
) -> str:
    if value is None:
        return "null"

    text: str = value.to_json()
    _reject_nul(_decoded_json(text))
    return text


def require_json_object_or_null(
    text: str,
    /,
) -> None:
    try:
        decoded: Any = _decoded_json(text)

    except Exception as exc:
        raise ValueError("Result is not valid JSON text") from exc

    if decoded is not None and not isinstance(decoded, dict):
        raise ValueError("Result has to be JSON text of an object or null")
