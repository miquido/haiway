__all__ = (
    "MissingContext",
    "MissingState",
)


class MissingContext(Exception):
    """
    Exception raised when attempting to access a context that doesn't exist.

    This exception is raised when code attempts to access the context system
    outside of an active context, such as trying to access state or scope
    identifiers when no context has been established.
    """


class MissingState(Exception):
    """
    Exception raised when attempting to access state that doesn't exist.

    This exception is raised when code attempts to access a specific state type
    that is not present in the current context and cannot be automatically
    created (either because no default was provided or instantiation failed).
    """
