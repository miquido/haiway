from logging import Logger
from time import monotonic
from typing import Any

from haiway.context import Observability, ObservabilityLevel, ScopeIdentifier
from haiway.state import State

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


def LoggerObservability(  # noqa: C901
    logger: Logger,
    /,
    *,
    summarize_context: bool = __debug__,
) -> Observability:
    root_scope: ScopeIdentifier | None = None
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
        assert scope.scope_id in scopes  # nosec: B101

        logger.log(
            level,
            f"{scope.unique_name} {message}",
            *args,
            exc_info=exception,
        )

    def event_recording(
        scope: ScopeIdentifier,
        /,
        *,
        level: ObservabilityLevel,
        event: State,
    ) -> None:
        assert root_scope is not None  # nosec: B101
        assert scope.scope_id in scopes  # nosec: B101

        event_str: str = f"Event:\n{event.to_str(pretty=True)}"
        if summarize_context:  # store only for summary
            scopes[scope.scope_id].store.append(event_str)

        logger.log(
            level,
            f"{scope.unique_name} {event_str}",
        )

    def metric_recording(
        scope: ScopeIdentifier,
        /,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
    ) -> None:
        assert root_scope is not None  # nosec: B101
        assert scope.scope_id in scopes  # nosec: B101

        metric_str: str = f"Metric - {metric}:{value}{unit or ''}"
        if summarize_context:  # store only for summary
            scopes[scope.scope_id].store.append(metric_str)

        logger.log(
            ObservabilityLevel.INFO,
            f"{scope.unique_name} {metric_str}",
        )

    def scope_entering[Metric: State](
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope.scope_id not in scopes  # nosec: B101
        scope_store: ScopeStore = ScopeStore(scope)
        scopes[scope.scope_id] = scope_store

        logger.log(
            ObservabilityLevel.INFO,
            f"{scope.unique_name} Entering scope: {scope.label}",
        )

        nonlocal root_scope
        if root_scope is None:
            root_scope = scope

        else:
            scopes[scope.parent_id].nested.append(scope_store)

    def scope_exiting[Metric: State](
        scope: ScopeIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        nonlocal root_scope
        nonlocal scopes
        assert root_scope is not None  # nosec: B101
        assert scope.scope_id in scopes  # nosec: B101

        scopes[scope.scope_id].exit()

        if not scopes[scope.scope_id].try_complete():
            return  # not completed yet or already completed

        logger.log(
            ObservabilityLevel.INFO,
            f"{scope.unique_name} Exiting scope: {scope.label}",
        )
        metric_str: str = f"Metric - scope_time:{scopes[scope.scope_id].time:.3f}s"
        if summarize_context:  # store only for summary
            scopes[scope.scope_id].store.append(metric_str)

        logger.log(
            ObservabilityLevel.INFO,
            f"{scope.unique_name} {metric_str}",
        )

        # try complete parent scopes
        parent_id: str = scope.parent_id
        while scopes[parent_id].try_complete():
            parent_id = scopes[parent_id].identifier.parent_id

        # check for root completion
        if scopes[root_scope.scope_id].completed:
            if summarize_context:
                logger.log(
                    ObservabilityLevel.DEBUG,
                    f"Observability summary:\n{_tree_summary(scopes[root_scope.scope_id])}",
                )

            # finished root - cleanup state
            root_scope = None
            scopes = {}

    return Observability(
        log_recording=log_recording,
        event_recording=event_recording,
        metric_recording=metric_recording,
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
