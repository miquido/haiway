from dataclasses import dataclass
from datetime import UTC, datetime
from textwrap import dedent
from typing import Annotated, Any
from uuid import UUID

import pytest

from haiway import Missing, Sensitive, State
from haiway.utils.formatting import escape_controls, format_log_message, format_str


def test_multiline_string_inside_sequence() -> None:
    formatted = format_str(["a\nb"])

    assert formatted == dedent(
        '''[
  [0]:
  """
    a
    b
  """
]'''
    )


def test_multiline_string_inside_mapping() -> None:
    formatted = format_str({"k": "a\nb"})

    assert formatted == dedent(
        '''{
  ["k"]:
  """
    a
    b
  """
}'''
    )


class _SlotsOnly:
    __slots__ = ("x",)

    def __init__(self, x: int) -> None:
        self.x = x

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"_SlotsOnly(x={self.x})"


def test_slot_object_in_mapping_respects_indentation() -> None:
    formatted = format_str({"k": _SlotsOnly(1)})

    assert formatted == dedent(
        """{
  ["k"]: _SlotsOnly(x=1)
}"""
    )


class _MultilineSlots:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "line 1\nline 2"


def test_multiline_object_repr_indents_wrapped_lines_only() -> None:
    formatted = format_str({"k": _MultilineSlots()})

    assert formatted == dedent(
        """{
  ["k"]:
line 1
  line 2
}"""
    )


def test_escape_controls_escapes_line_breaks() -> None:
    assert escape_controls("first\nsecond") == "first\\nsecond"
    assert escape_controls("overwrite\rhidden") == "overwrite\\rhidden"


def test_escape_controls_escapes_terminal_sequences() -> None:
    assert escape_controls("\x1b[2Kwiped") == "\\x1b[2Kwiped"
    assert escape_controls("bell\x07") == "bell\\x07"


def test_escape_controls_keeps_tabs_and_printable_text() -> None:
    assert escape_controls("column\tvalue") == "column\tvalue"
    assert escape_controls("zażółć gęślą jaźń") == "zażółć gęślą jaźń"


def test_escape_controls_may_keep_newlines() -> None:
    assert escape_controls("first\nsecond", allow_newlines=True) == "first\nsecond"
    assert escape_controls("first\rsecond", allow_newlines=True) == "first\\rsecond"


def test_format_log_message_escapes_control_characters() -> None:
    forged = "alice\n2026-08-20 [ERROR] AUDIT: admin login succeeded"

    assert "\n" not in format_log_message(f"login attempt for {forged}")


def test_format_log_message_interpolates_arguments() -> None:
    assert format_log_message("processed %d of %s", (3, "items")) == "processed 3 of items"


def test_format_log_message_keeps_record_of_mismatched_format() -> None:
    # a malformed format string must not lose the message
    formatted = format_log_message("user said: %s and %d", ("x",))

    assert "user said: %s and %d" in formatted
    assert "'x'" in formatted


def test_format_log_message_does_not_interpolate_without_arguments() -> None:
    assert format_log_message("100%s of progress") == "100%s of progress"


def test_format_str_escapes_control_characters_of_strings() -> None:
    assert format_str("first\rsecond") == '"first\\rsecond"'
    assert "\x1b" not in format_str({"key": "\x1b[31mred"})


def test_format_str_keeps_multiline_blocks() -> None:
    assert format_str("first\nsecond") == '"""\n  first\n  second\n"""'


def test_booleans_render_as_their_names() -> None:
    # bool is a subclass of int, so it shares the numeric branch - it still has
    # to read as True/False rather than as 1/0
    assert format_str(True) == "True"
    assert format_str(False) == "False"
    assert format_str({"flag": True, "off": False}) == dedent(
        """{
  ["flag"]: True
  ["off"]: False
}"""
    )


class _PlainObject:
    def __init__(self) -> None:
        self.visible = 1
        self._private = 2
        self.absent: object = Missing
        self.text = "x\ny"


class _OpaqueObject:
    __slots__ = ()

    def __str__(self) -> str:
        return "first\nsecond"


class _SecretState(State):
    name: str
    secret: Annotated[str, Sensitive()]


@dataclass
class _DataclassObject:
    value: int


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "None"),
        (Missing, ""),
        (1, "1"),
        (1.5, "1.5"),
        (1j, "1j"),
        (True, "True"),
        (b"abc", "<<<3 bytes>>>"),
        (bytearray(2), "<<<2 bytes>>>"),
        (memoryview(b"abcd"), "<<<4 bytes>>>"),
        (UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        (datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC), "2024-01-02T03:04:05+00:00"),
        ("text", '"text"'),
        ("", '""'),
        ({}, "{}"),
        ([], "[]"),
        ((), "[]"),
        (set[int](), "{}"),
        (frozenset[int](), "{}"),
    ],
)
def test_format_str_scalars_and_empty_containers(value: Any, expected: str) -> None:
    assert format_str(value) == expected


def test_format_str_renders_sets_as_braces() -> None:
    assert format_str({"a"}) == '{\n  "a"\n}'
    assert format_str(frozenset({1})) == "{\n  1\n}"


def test_format_str_renders_all_elements_of_a_set() -> None:
    formatted = format_str({1, 2, 3})

    assert formatted.startswith("{\n")
    assert formatted.endswith("\n}")
    for element in ("1", "2", "3"):
        assert f"  {element}" in formatted


def test_format_str_skips_missing_elements_and_values() -> None:
    assert format_str([Missing, 1]) == "[\n  [1]: 1\n]"
    assert format_str({"k": Missing}) == "{}"
    assert format_str({"k": Missing, "other": 1}) == '{\n  ["other"]: 1\n}'
    assert format_str([Missing]) == "[]"
    assert format_str({Missing}) == "{}"


def test_format_str_applies_indent_to_nested_output() -> None:
    assert format_str([1, 2], indent=2) == "  [\n    [0]: 1\n    [1]: 2\n  ]"


def test_format_str_nested_sequences() -> None:
    assert (
        format_str([[1], [2]]) == "[\n  [0]:\n  [\n    [0]: 1\n  ]\n  [1]:\n  [\n    [0]: 2\n  ]\n]"
    )


def test_format_str_object_with_instance_dict() -> None:
    formatted = format_str(_PlainObject())

    assert formatted == '┍━ _PlainObject:\n┝ visible: 1\n┝ text:\n  """\n    x\n    y\n  """\n┕━'
    assert "_private" not in formatted
    assert "absent" not in formatted


def test_format_str_object_without_attributes_falls_back_to_str() -> None:
    assert format_str(_OpaqueObject()) == "first\nsecond"
    assert format_str(_OpaqueObject(), indent=2) == "first\n  second"


def test_format_str_dataclass_object() -> None:
    assert format_str(_DataclassObject(value=1)) == "┍━ _DataclassObject:\n┝ value: 1\n┕━"


def test_format_str_state_redacts_sensitive_attributes() -> None:
    formatted = format_str(_SecretState(name="public", secret="hunter2"))

    assert "hunter2" not in formatted
    assert '┝ name: "public"' in formatted
    assert "┝ secret: <redacted>" in formatted


def test_format_str_object_inside_collections() -> None:
    formatted = format_str({"key": _DataclassObject(value=1)})

    assert "┍━ _DataclassObject:" in formatted
    assert "┝ value: 1" in formatted
    assert formatted.startswith('{\n  ["key"]:')


def test_format_str_nested_empty_containers_stay_inline() -> None:
    assert format_str({"a": {}}) == '{\n  ["a"]: {}\n}'
    assert format_str({"a": []}) == '{\n  ["a"]: []\n}'
    assert format_str([{}, set()]) == "[\n  [0]: {}\n  [1]: {}\n]"
    assert format_str(_PlainObject2()) == "┍━ _PlainObject2:\n┝ items: []\n┝ mapping: {}\n┕━"


class _PlainObject2:
    def __init__(self) -> None:
        self.items: list[int] = []
        self.mapping: dict[str, int] = {}


@pytest.mark.parametrize("separator", [chr(0x2028), chr(0x2029)])
@pytest.mark.parametrize("allow_newlines", [True, False])
def test_escape_controls_escapes_unicode_line_separators(
    separator: str,
    allow_newlines: bool,
) -> None:
    # `str.splitlines` and many log viewers break lines at them - a forged record otherwise
    escaped: str = escape_controls(f"value{separator}forged", allow_newlines=allow_newlines)
    assert len(escaped.splitlines()) == 1
    assert escaped == f"value\\u{ord(separator):04x}forged"
