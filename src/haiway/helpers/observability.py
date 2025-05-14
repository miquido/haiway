from collections.abc import Mapping
from logging import Logger, getLogger
from time import monotonic
from typing import Any

from haiway.context import Observability, ObservabilityLevel, ScopeIdentifier
from haiway.context.observability import ObservabilityAttribute
from haiway.state import State
from haiway.utils.formatting import format_str

__all__ = ("LoggerObservability",)


class ScopeStore:
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
        return (self._completed or monotonic()) - self.entered

    @property
    def exited(self) -> bool:
        return self._exited is not None

    def exit(self) -> None:
        assert self._exited is None  # nosec: B101
        self._exited = monotonic()

    @property
    def completed(self) -> bool:
        return self._completed is not None and all(nested.completed for nested in self.nested)

    def try_complete(self) -> bool:
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
    root_scope: ScopeIdentifier | None = None
    root_logger: Logger | None = logger
    scopes: dict[str, ScopeStore] = {}

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
            f"{scope.unique_name} {message}",
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
            f"{scope.unique_name} {event_str}",
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
            metric_str = f"Metric: {metric} = {value}{unit or ''}\n{format_str(attributes)}"

        else:
            metric_str = f"Metric: {metric} = {value}{unit or ''}"

        if debug_context:  # store only for summary
            scopes[scope.scope_id].store.append(metric_str)

        root_logger.log(
            level,
            f"{scope.unique_name} {metric_str}",
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
            attributes_str,
        )

    def scope_entering[Metric: State](
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
            root_logger = logger or getLogger(scope.label)

        else:
            scopes[scope.parent_id].nested.append(scope_store)

        assert root_logger is not None  # nosec: B101
        root_logger.log(
            ObservabilityLevel.INFO,
            f"{scope.unique_name} Entering scope: {scope.label}",
        )

    def scope_exiting[Metric: State](
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
            f"{scope.unique_name} Exiting scope: {scope.label}",
        )
        metric_str: str = f"Metric - scope_time:{scopes[scope.scope_id].time:.3f}s"
        if debug_context:  # store only for summary
            scopes[scope.scope_id].store.append(metric_str)

        root_logger.log(
            ObservabilityLevel.INFO,
            f"{scope.unique_name} {metric_str}",
        )

        # try complete parent scopes
        if scope != root_scope:
            parent_id: str = scope.parent_id
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
        log_recording=log_recording,
        event_recording=event_recording,
        metric_recording=metric_recording,
        attributes_recording=attributes_recording,
        scope_entering=scope_entering,
        scope_exiting=scope_exiting,
    )


def _tree_summary(scope_store: ScopeStore) -> str:
    elements: list[str] = [
        f"┍━ {scope_store.identifier.label} [{scope_store.identifier.scope_id}]:"
    ]
    for element in scope_store.store:
        if not element:
            continue  # skip empty

        elements.append(f"┝ {element.replace('\n', '\n|  ')}")

    for nested in scope_store.nested:
        nested_summary: str = _tree_summary(nested)

        elements.append(f"|  {nested_summary.replace('\n', '\n|  ')}")

    return "\n".join(elements) + "\n┕━"
