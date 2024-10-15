__all__ = [
    "MissingContext",
    "MissingDependency",
    "MissingState",
]


class MissingContext(Exception):
    pass


class MissingDependency(Exception):
    pass


class MissingState(Exception):
    pass
