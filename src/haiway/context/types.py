__all__ = (
    "ContextException",
    "ContextMissing",
    "ContextStateMissing",
)


class ContextException(Exception):
    """
    Base exception raised from invalid context interactions.
    """


class ContextMissing(ContextException):
    """
    Exception raised when attempting to access a context that doesn't exist.
    """


class ContextStateMissing(ContextException):
    """
    Exception raised when attempting to access state that doesn't exist.
    """
