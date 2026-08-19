from typing import Any, NoReturn, final

__all__ = ("Sensitive",)


@final
class Sensitive:
    """
    Immutable annotation marking a value as sensitive.

    An attribute annotated with ``Sensitive`` keeps its value fully available
    through attribute access and serialization, while every human-readable
    rendering provided by Haiway - ``State.__str__``, ``State.__repr__`` and
    ``format_str`` - replaces it with the redaction. Use it for credentials,
    tokens, and personal data which must never reach logs or observability
    backends by accident.

    Parameters
    ----------
    redaction : str, default="<redacted>"
        Text rendered instead of the value. Provide a more specific marker when
        the shape of the omission carries useful meaning, i.e. ``"<api-key>"``.
        Must not be empty - an empty marker is indistinguishable from a missing
        one for the renderers consuming it.

    Raises
    ------
    ValueError
        When ``redaction`` is empty.

    Every parameter has a default, so the class itself marks an attribute the
    same way an instance of it does - ``Annotated[str, Sensitive]`` and
    ``Annotated[str, Sensitive()]`` are equivalent. Write an instance when the
    redaction has to differ from the default one.

    Examples
    --------
    >>> class Credentials(State):
    ...     user: str
    ...     api_key: Annotated[str, Sensitive]
    ...     refresh_token: Annotated[str, Sensitive(redaction="<token>")]
    >>> str(Credentials(user="alice", api_key="sk-live-1", refresh_token="rt-1"))
    'Credentials(user: alice, api_key: <redacted>, refresh_token: <token>)'

    Notes
    -----
    Marking an attribute sensitive affects rendering only. ``to_mapping``,
    ``to_json`` and direct attribute access intentionally return the actual
    value, so persistence and outgoing requests keep working - review those call
    sites separately when handling secrets.
    """

    __slots__ = ("redaction",)

    def __init__(
        self,
        *,
        redaction: str = "<redacted>",
    ) -> None:
        # not an assertion - an optimized build would strip the check and let an
        # empty redaction through, making the renderers omit the value entirely
        # instead of marking it as withheld
        if not redaction:
            raise ValueError("Sensitive redaction can't be empty")

        self.redaction: str
        object.__setattr__(
            self,
            "redaction",
            redaction,
        )

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> NoReturn:
        raise AttributeError("Sensitive can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> NoReturn:
        raise AttributeError("Sensitive can't be modified")
