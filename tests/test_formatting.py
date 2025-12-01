from textwrap import dedent

from haiway.utils.formatting import format_str


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
