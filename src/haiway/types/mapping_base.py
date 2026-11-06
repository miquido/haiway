import builtins
from typing import TYPE_CHECKING, Any, Final

__all__ = (
    "FROZEN",
    "MapBase",
    "plain_repr",
)

# The builtin `frozendict` is what an immutable mapping is made of where there is one -
# it can't be mutated through the `dict` methods the way a `dict` subclass can. Its
# absence leaves a `dict`, guarded by the overrides of whatever subclasses it. This is
# the one place which decides, and says which of the two it was.
if TYPE_CHECKING:
    MapBase = dict

else:
    MapBase = getattr(builtins, "frozendict", dict)

FROZEN: Final[bool] = MapBase is not dict


def plain_repr(mapping: Any) -> str:
    """The way a `dict` is printed, which `frozendict` does with the name of its class."""
    return repr({**mapping})
