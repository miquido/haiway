from collections.abc import Sequence
from itertools import chain
from time import monotonic
from typing import Any, Self, cast, final, overload

from haiway.context import MetricsHandler, ScopeIdentifier, ctx
from haiway.state import State
from haiway.types import MISSING

__all_ = [
    "MetricsLogger",
    "MetricsHolder",
]


class MetricsScopeStore:
    __slots__ = (
        "entered",
        "exited",
        "identifier",
        "metrics",
        "nested",
    )

    def __init__(
        self,
        identifier: ScopeIdentifier,
        /,
    ) -> None:
        self.identifier: ScopeIdentifier = identifier
        self.entered: float = monotonic()
        self.metrics: dict[type[State], State] = {}
        self.exited: float | None = None
        self.nested: list[MetricsScopeStore] = []

    @property
    def time(self) -> float:
        return (self.exited or monotonic()) - self.entered

    @property
    def finished(self) -> float:
        return self.exited is not None and all(nested.finished for nested in self.nested)

    @overload
    def merged[Metric: State](
        self,
    ) -> Sequence[State]: ...

    @overload
    def merged[Metric: State](
        self,
        metric: type[Metric],
    ) -> Metric | None: ...

    def merged[Metric: State](
        self,
        metric: type[Metric] | None = None,
    ) -> Sequence[State] | Metric | None:
        if metric is None:
            merged_metrics: dict[type[State], State] = dict(self.metrics)
            for nested in chain.from_iterable(nested.merged() for nested in self.nested):
                metric_type: type[State] = type(nested)
                current: State | None = merged_metrics.get(metric_type)

                if current is None:
                    merged_metrics[metric_type] = nested
                    continue  # keep going

                if hasattr(current, "__add__"):
                    merged_metrics[metric_type] = current.__add__(nested)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                    assert isinstance(merged_metrics[metric_type], State)  # nosec: B101
                    continue  # keep going

                break  # we have multiple value without a way to merge

            return tuple(merged_metrics.values())

        else:
            merged_metric: State | None = self.metrics.get(metric)
            for nested in self.nested:
                nested_metric: Metric | None = nested.merged(metric)
                if nested_metric is None:
                    continue  # skip missing

                if merged_metric is None:
                    merged_metric = nested_metric
                    continue  # keep going

                if hasattr(merged_metric, "__add__"):
                    merged_metric = merged_metric.__add__(nested_metric)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue, reportUnknownVariableType]
                    assert isinstance(merged_metric, metric)  # nosec: B101
                    continue  # keep going

                break  # we have multiple value without a way to merge

            return cast(Metric | None, merged_metric)


@final
class MetricsHolder:
    @classmethod
    def handler(cls) -> MetricsHandler:
        store_handler: Self = cls()
        return MetricsHandler(
            record=store_handler.record,
            read=store_handler.read,
            enter_scope=store_handler.enter_scope,
            exit_scope=store_handler.exit_scope,
        )

    __slots__ = (
        "root_scope",
        "scopes",
    )

    def __init__(self) -> None:
        self.root_scope: ScopeIdentifier | None = None
        self.scopes: dict[ScopeIdentifier, MetricsScopeStore] = {}

    def record(
        self,
        scope: ScopeIdentifier,
        /,
        metric: State,
    ) -> None:
        assert self.root_scope is not None  # nosec: B101
        assert scope in self.scopes  # nosec: B101

        metric_type: type[State] = type(metric)
        metrics: dict[type[State], State] = self.scopes[scope].metrics
        if (current := metrics.get(metric_type)) and hasattr(current, "__add__"):
            metrics[type(metric)] = current.__add__(metric)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

        metrics[type(metric)] = metric

    async def read[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
        *,
        metric: type[Metric],
        merged: bool,
    ) -> Metric | None:
        assert self.root_scope is not None  # nosec: B101
        assert scope in self.scopes  # nosec: B101

        if merged:
            return self.scopes[scope].merged(metric)

        else:
            return cast(Metric | None, self.scopes[scope].metrics.get(metric))

    def enter_scope[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope not in self.scopes  # nosec: B101
        scope_metrics = MetricsScopeStore(scope)
        self.scopes[scope] = scope_metrics

        if self.root_scope is None:
            self.root_scope = scope

        else:
            for key in self.scopes.keys():
                if key.scope_id == scope.parent_id:
                    self.scopes[key].nested.append(scope_metrics)
                    return

            ctx.log_debug(
                "Attempting to enter nested scope metrics without entering its parent first"
            )

    def exit_scope[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope in self.scopes  # nosec: B101
        self.scopes[scope].exited = monotonic()


@final
class MetricsLogger:
    @classmethod
    def handler(
        cls,
        items_limit: int | None = None,
        redact_content: bool = False,
    ) -> MetricsHandler:
        logger_handler: Self = cls(
            items_limit=items_limit,
            redact_content=redact_content,
        )
        return MetricsHandler(
            record=logger_handler.record,
            read=logger_handler.read,
            enter_scope=logger_handler.enter_scope,
            exit_scope=logger_handler.exit_scope,
        )

    __slots__ = (
        "items_limit",
        "redact_content",
        "root_scope",
        "scopes",
    )

    def __init__(
        self,
        items_limit: int | None,
        redact_content: bool,
    ) -> None:
        self.root_scope: ScopeIdentifier | None = None
        self.scopes: dict[ScopeIdentifier, MetricsScopeStore] = {}
        self.items_limit: int | None = items_limit
        self.redact_content: bool = redact_content

    def record(
        self,
        scope: ScopeIdentifier,
        /,
        metric: State,
    ) -> None:
        assert self.root_scope is not None  # nosec: B101
        assert scope in self.scopes  # nosec: B101

        metric_type: type[State] = type(metric)
        metrics: dict[type[State], State] = self.scopes[scope].metrics
        if (current := metrics.get(metric_type)) and hasattr(current, "__add__"):
            metrics[type(metric)] = current.__add__(metric)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

        metrics[type(metric)] = metric
        if log := _state_log(
            metric,
            list_items_limit=self.items_limit,
            redact_content=self.redact_content,
        ):
            ctx.log_debug(f"Recorded metric:\n⎡ {type(metric).__qualname__}:{log}\n⌊")

    async def read[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
        *,
        metric: type[Metric],
        merged: bool,
    ) -> Metric | None:
        assert self.root_scope is not None  # nosec: B101
        assert scope in self.scopes  # nosec: B101

        if merged:
            return self.scopes[scope].merged(metric)

        else:
            return cast(Metric | None, self.scopes[scope].metrics.get(metric))

    def enter_scope[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope not in self.scopes  # nosec: B101
        scope_metrics = MetricsScopeStore(scope)
        self.scopes[scope] = scope_metrics

        if self.root_scope is None:
            self.root_scope = scope

        else:
            for key in self.scopes.keys():
                if key.scope_id == scope.parent_id:
                    self.scopes[key].nested.append(scope_metrics)
                    return

            ctx.log_debug(
                "Attempting to enter nested scope metrics without entering its parent first"
            )

    def exit_scope[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope in self.scopes  # nosec: B101
        self.scopes[scope].exited = monotonic()

        if scope == self.root_scope and self.scopes[scope].finished:
            if log := _tree_log(
                self.scopes[scope],
                list_items_limit=self.items_limit,
                redact_content=self.redact_content,
            ):
                ctx.log_debug(f"Metrics summary:\n{log}")


def _tree_log(
    metrics: MetricsScopeStore,
    list_items_limit: int | None,
    redact_content: bool,
) -> str:
    log: str = (
        f"⎡ @{metrics.identifier.label} [{metrics.identifier.scope_id}]({metrics.time:.2f}s):"
    )

    for metric in metrics.merged():
        if type(metric) not in metrics.metrics:
            continue  # skip metrics not available in this scope

        metric_log: str = ""
        for key, value in vars(metric).items():
            if value_log := _value_log(
                value,
                list_items_limit=list_items_limit,
                redact_content=redact_content,
            ):
                metric_log += f"\n├ {key}: {value_log}"

            else:
                continue  # skip empty values

        if not metric_log:
            continue  # skip empty logs

        log += f"\n⎡ •{type(metric).__qualname__}:{metric_log.replace('\n', '\n|  ')}\n⌊"

    for nested in metrics.nested:
        nested_log: str = _tree_log(
            nested,
            list_items_limit=list_items_limit,
            redact_content=redact_content,
        )

        log += f"\n\n{nested_log}"

    return log.strip().replace("\n", "\n|  ") + "\n⌊"


def _state_log(
    value: State,
    /,
    list_items_limit: int | None,
    redact_content: bool,
) -> str | None:
    state_log: str = ""
    for key, element in vars(value).items():
        element_log: str | None = _value_log(
            element,
            list_items_limit=list_items_limit,
            redact_content=redact_content,
        )

        if element_log:
            state_log += f"\n├ {key}: {element_log}"

        else:
            continue  # skip empty logs

    if state_log:
        return state_log

    else:
        return None  # skip empty logs


def _dict_log(
    value: dict[Any, Any],
    /,
    list_items_limit: int | None,
    redact_content: bool,
) -> str | None:
    dict_log: str = ""
    for key, element in value.items():
        element_log: str | None = _value_log(
            element,
            list_items_limit=list_items_limit,
            redact_content=redact_content,
        )
        if element_log:
            dict_log += f"\n[{key}]: {element_log}"

        else:
            continue  # skip empty logs

    if dict_log:
        return dict_log.replace("\n", "\n|  ")

    else:
        return None  # skip empty logs


def _list_log(
    value: list[Any],
    /,
    list_items_limit: int | None,
    redact_content: bool,
) -> str | None:
    list_log: str = ""
    enumerated: list[tuple[int, Any]] = list(enumerate(value))
    if list_items_limit:
        if list_items_limit > 0:
            enumerated = enumerated[:list_items_limit]

        else:
            enumerated = enumerated[list_items_limit:]

    for idx, element in enumerated:
        element_log: str | None = _value_log(
            element,
            list_items_limit=list_items_limit,
            redact_content=redact_content,
        )
        if element_log:
            list_log += f"\n[{idx}] {element_log}"

        else:
            continue  # skip empty logs

    if list_log:
        return list_log.replace("\n", "\n|  ")

    else:
        return None  # skip empty logs


def _raw_value_log(
    value: Any,
    /,
    redact_content: bool,
) -> str | None:
    if value is MISSING:
        return None  # skip missing

    if redact_content:
        return "[redacted]"

    elif isinstance(value, str):
        return f'"{value}"'.replace("\n", "\n|  ")

    else:
        return str(value).strip().replace("\n", "\n|  ")


def _value_log(
    value: Any,
    /,
    list_items_limit: int | None,
    redact_content: bool,
) -> str | None:
    # try unpack dicts
    if isinstance(value, dict):
        return _dict_log(
            cast(dict[Any, Any], value),
            list_items_limit=list_items_limit,
            redact_content=redact_content,
        )

    # try unpack lists
    elif isinstance(value, list):
        return _list_log(
            cast(list[Any], value),
            list_items_limit=list_items_limit,
            redact_content=redact_content,
        )

    # try unpack state
    elif isinstance(value, State):
        return _state_log(
            value,
            list_items_limit=list_items_limit,
            redact_content=redact_content,
        )

    else:
        return _raw_value_log(
            value,
            redact_content=redact_content,
        )
