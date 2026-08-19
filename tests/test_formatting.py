from textwrap import dedent

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
