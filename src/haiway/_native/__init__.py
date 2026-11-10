from haiway._native import _nstruct as nstruct

__all__ = ("nstruct",)

# The compiled extension is the storage of `State` and `Immutable` - there is no other
# implementation. A build without it does not import.
