from collections.abc import Mapping
from reprlib import recursive_repr
from typing import TYPE_CHECKING

__all__ = (
    "MapBase",
    "plain_repr",
)

# The builtin `frozendict` is what an immutable mapping is made of - it can't be mutated
# through the `dict` methods the way a `dict` subclass can. A type checker is told it is a
# `dict`, which is the interface it has for reading.
if TYPE_CHECKING:
    MapBase = dict

else:
    MapBase = frozendict


@recursive_repr()
def plain_repr[Key, Element](mapping: Mapping[Key, Element]) -> str:
    """The way a `dict` is printed, which `frozendict` does with the name of its class."""
    return repr({**mapping})
