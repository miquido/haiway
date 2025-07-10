from collections.abc import Mapping
from logging import Logger, getLogger
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from haiway.context import Observability, ObservabilityLevel, ScopeIdentifier
from haiway.context.observability import ObservabilityAttribute
from haiway.utils.formatting import format_str

__all__ = ("LoggerObservability",)


class ScopeStore:
    """
    Internal class for storing scope information during observability tracking.

    Tracks timing information, nested scopes, and recorded events for a specific scope.
    Used by LoggerObservability to maintain the hierarchy of scopes and their data.
    """

    __slots__ = (
        "_completed",
        "_exited",
        "entered",
        "identifier",
        "nested",
        "store",
    )

    def __init__(
        self,
        identifier: ScopeIdentifier,
        /,
    ) -> None:
        self.identifier: ScopeIdentifier = identifier
        self.nested: list[ScopeStore] = []
        self.entered: float = monotonic()
        self._exited: float | None = None
        self._completed: float | None = None
        self.store: list[str] = []

    @property
    def time(self) -> float:
        """Calculate the elapsed time in seconds since this scope was entered."""
        return (self._completed or monotonic()) - self.entered

    @property
    def exited(self) -> bool:
        """Check if this scope has been exited."""
        return self._exited is not None

    def exit(self) -> None:
        """Mark this scope as exited and record the exit time."""
        assert self._exited is None  # nosec: B101
        self._exited = monotonic()

    @property
    def completed(self) -> bool:
        """
        Check if this scope and all its nested scopes are completed.

        A scope is considered completed when it has been exited and all its
        nested scopes have also been completed.
        """
        return self._completed is not None and all(nested.completed for nested in self.nested)

    def try_complete(self) -> bool:
        """
        Try to mark this scope as completed.

        A scope can only be completed if:
        - It has been exited
        - It has not already been completed
        - All its nested scopes are completed

        Returns
        -------
        bool
            True if the scope was successfully marked as completed,
            False if any completion condition was not met
        """
        if self._exited is None:
            return False  # not elegible for completion yet

        if self._completed is not None:
            return False  # already completed

        if not all(nested.completed for nested in self.nested):
            return False  # nested not completed

        self._completed = monotonic()
        return True  # successfully completed


def LoggerObservability(  # noqa: C901, PLR0915
    logger: Logger | None = None,
    /,
    *,
    debug_context: bool = __debug__,
) -> Observability:
    """
    Create an Observability implementation that uses a standard Python logger.

    This factory function creates an Observability instance that uses a Logger for recording
    various types of observability data including logs, events, metrics, and attributes.
    It maintains a hierarchical scope structure that tracks timing information and provides
    a summary of all recorded data when the root scope exits.

    Parameters
    ----------
    logger: Logger | None
        The logger to use for recording observability data. If None, a logger will be
        created based on the scope label when the first scope is entered.
    debug_context: bool
        Whether to store and display a detailed hierarchical summary when the root scope
        exits. Defaults to True in debug mode (__debug__) and False otherwise.

    Returns
    -------
    Observability
        An Observability instance that uses the specified logger (or a default one)
        for recording observability data.

    Notes
    -----
    The created Observability instance tracks timing for each scope and records it
    when the scope exits. When the root scope exits and debug_context is True,
    it produces a hierarchical summary of all recorded events, metrics, and attributes.
    """
    root_scope: ScopeIdentifier | None = None
    root_logger: Logger | None = logger
    scopes: dict[UUID, ScopeStore] = {}

    trace_id: UUID = uuid4()
    trace_id_hex: str = trace_id.hex

    def trace_identifying(
        scope: ScopeIdentifier,
        /,
    ) -> UUID:
        return trace_id

    def log_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        assert root_scope is not None  # nosec: B101
        assert root_logger is not None  # nosec: B101
        assert scope.scope_id in scopes  # nosec: B101

        root_logger.log(
            level,
            f"[{trace_id_hex}] {scope.unique_name} {message}",
            *args,
            exc_info=exception,
        )

    def event_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        assert root_scope is not None  # nosec: B101
        assert root_logger is not None  # nosec: B101
        assert scope.scope_id in scopes  # nosec: B101

        event_str: str = f"Event: {event} {format_str(attributes)}"
        if debug_context:  # store only for summary
            scopes[scope.scope_id].store.append(event_str)

        root_logger.log(
            level,
            f"[{trace_id_hex}] {scope.unique_name} {event_str}",
        )

    def metric_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        assert root_scope is not None  # nosec: B101
        assert root_logger is not None  # nosec: B101
        assert scope.scope_id in scopes  # nosec: B101

        metric_str: str
        if attributes:
            metric_str = f"Metric: {metric} = {value} {unit or ''}\n{format_str(attributes)}"

        else:
            metric_str = f"Metric: {metric} = {value} {unit or ''}"

        if debug_context:  # store only for summary
            scopes[scope.scope_id].store.append(metric_str)

        root_logger.log(
            level,
            f"[{trace_id_hex}] {scope.unique_name} {metric_str}",
        )

    def attributes_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        assert root_scope is not None  # nosec: B101
        assert root_logger is not None  # nosec: B101

        if not attributes:
            return

        attributes_str: str = f"Attributes: {format_str(attributes)}"
        if debug_context:  # store only for summary
            scopes[scope.scope_id].store.append(attributes_str)

        root_logger.log(
            level,
            f"[{trace_id_hex}] {scope.unique_name} {attributes_str}",
        )

    def scope_entering(
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope.scope_id not in scopes  # nosec: B101
        scope_store: ScopeStore = ScopeStore(scope)
        scopes[scope.scope_id] = scope_store

        nonlocal root_scope
        nonlocal root_logger
        if root_scope is None:
            root_scope = scope
            root_logger = logger or getLogger(scope.name)

        else:
            scopes[scope.parent_id].nested.append(scope_store)

        assert root_logger is not None  # nosec: B101
        root_logger.log(
            ObservabilityLevel.INFO,
            f"[{trace_id_hex}] {scope.unique_name} Entering scope: {scope.name}",
        )

    def scope_exiting(
        scope: ScopeIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        nonlocal root_scope
        nonlocal root_logger
        nonlocal scopes
        assert root_scope is not None  # nosec: B101
        assert root_logger is not None  # nosec: B101
        assert scope.scope_id in scopes  # nosec: B101

        scopes[scope.scope_id].exit()

        if not scopes[scope.scope_id].try_complete():
            return  # not completed yet or already completed

        root_logger.log(
            ObservabilityLevel.INFO,
            f"[{trace_id_hex}] {scope.unique_name} Exiting scope: {scope.name}",
        )
        metric_str: str = f"Metric - scope_time:{scopes[scope.scope_id].time:.3f}s"
        if debug_context:  # store only for summary
            scopes[scope.scope_id].store.append(metric_str)

        root_logger.log(
            ObservabilityLevel.INFO,
            f"[{trace_id_hex}] {scope.unique_name} {metric_str}",
        )

        # try complete parent scopes
        if scope != root_scope:
            parent_id: UUID = scope.parent_id
            while scopes[parent_id].try_complete():
                if scopes[parent_id].identifier == root_scope:
                    break

                parent_id = scopes[parent_id].identifier.parent_id

        # check for root completion
        if scopes[root_scope.scope_id].completed:
            if debug_context:
                root_logger.log(
                    ObservabilityLevel.DEBUG,
                    f"Observability summary:\n{_tree_summary(scopes[root_scope.scope_id])}",
                )

            # finished root - cleanup state
            root_scope = None
            root_logger = None
            scopes = {}

    return Observability(
        trace_identifying=trace_identifying,
        log_recording=log_recording,
        event_recording=event_recording,
        metric_recording=metric_recording,
        attributes_recording=attributes_recording,
        scope_entering=scope_entering,
        scope_exiting=scope_exiting,
    )


def _tree_summary(scope_store: ScopeStore) -> str:
    """
    Generate a hierarchical text representation of a scope and its nested scopes.

    Parameters
    ----------
    scope_store: ScopeStore
        The scope store to generate a summary for

    Returns
    -------
    str
        A formatted string representation of the scope hierarchy with recorded events
    """
    elements: list[str] = [f"┍━ {scope_store.identifier.name} [{scope_store.identifier.scope_id}]:"]
    for element in scope_store.store:
        if not element:
            continue  # skip empty

        elements.append(f"┝ {element.replace('\n', '\n|  ')}")

    for nested in scope_store.nested:
        nested_summary: str = _tree_summary(nested)

        elements.append(f"|  {nested_summary.replace('\n', '\n|  ')}")

    return "\n".join(elements) + "\n┕━"
