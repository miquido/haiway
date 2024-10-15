__all__ = [
    "frozenlist",
]

type frozenlist[Value] = tuple[Value, ...]
