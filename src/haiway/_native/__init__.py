import os
from types import ModuleType
from typing import Final

__all__ = ("NATIVE", "nstruct")

# The pure Python implementation is what runs, unless the native one is asked for:
# HAIWAY_NATIVE=1 uses the compiled extension and fails when it is not built, anything
# else - HAIWAY_NATIVE=0 included - leaves it unused, whether it is built or not.
nstruct: ModuleType | None = None
if os.environ.get("HAIWAY_NATIVE", "").strip() == "1":
    from haiway._native import _nstruct as nstruct  # pyright: ignore[reportAttributeAccessIssue]

# whether it is used - decided by the extension alone, not repeated anywhere
NATIVE: Final[bool] = nstruct is not None
