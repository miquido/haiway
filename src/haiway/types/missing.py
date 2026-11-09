from typing import Final

__all__ = ("Missing",)

Missing: Final = sentinel("Missing")
"""
The absence of a value, as opposed to ``None`` - a value which was not provided.

``Missing`` is both the value and its type, the way ``None`` is: a parameter which may
be left out is annotated ``int | Missing`` and defaults to ``Missing``. Compare it by
identity, ``value is Missing`` - it is a PEP 661 sentinel, the one instance there is,
kept as itself when copied or pickled. Unlike ``None`` it is truthy, so it is never
tested with ``if value:``.

Within a ``match`` statement, refer to it through its module - ``case haiway.Missing:`` -
as a bare ``case Missing:`` captures the value into a new variable instead.
"""
