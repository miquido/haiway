from collections.abc import Sequence
from itertools import chain
from time import monotonic
from typing import Any, Self, cast, final

from haiway.context import MetricsHandler, ScopeIdentifier, ctx
from haiway.state import State
from haiway.types import MISSING, Missing

__all_ = [
    "MetricsLogger",
]


class MetricsScopeStore:
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

    def merged(self) -> Sequence[State]:
        merged_metrics: dict[type[State], State] = dict(self.metrics)
        for element in chain.from_iterable(nested.merged() for nested in self.nested):
            metric_type: type[State] = type(element)
            current: State | Missing = merged_metrics.get(
                metric_type,
                MISSING,
            )

            if current is MISSING:
                continue  # do not merge to missing

            elif hasattr(current, "__add__"):
                merged_metrics[metric_type] = current.__add__(element)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

            else:
                merged_metrics[metric_type] = element

        return tuple(merged_metrics.values())


@final
class MetricsLogger:
    @classmethod
    def handler(
        cls,
        items_limit: int | None = None,
        item_character_limit: int | None = None,
    ) -> MetricsHandler:
        logger_handler: Self = cls(
            items_limit=items_limit,
            item_character_limit=item_character_limit,
        )
        return MetricsHandler(
            record=logger_handler.record,
            enter_scope=logger_handler.enter_scope,
            exit_scope=logger_handler.exit_scope,
        )

    def __init__(
        self,
        items_limit: int | None = None,
        item_character_limit: int | None = None,
    ) -> None:
        self.items_limit: int | None = items_limit
        self.item_character_limit: int | None = item_character_limit
        self.scopes: dict[ScopeIdentifier, MetricsScopeStore] = {}

    def record(
        self,
        scope: ScopeIdentifier,
        /,
        metric: State,
    ) -> None:
        assert scope in self.scopes  # nosec: B101
        metric_type: type[State] = type(metric)
        metrics: dict[type[State], State] = self.scopes[scope].metrics
        if (current := metrics.get(metric_type)) and hasattr(current, "__add__"):
            metrics[type(metric)] = current.__add__(metric)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

        metrics[type(metric)] = metric
        if __debug__:
            if log := _state_log(
                metric,
                list_items_limit=self.items_limit,
                item_character_limit=self.item_character_limit,
            ):
                ctx.log_info(f"Recorded:\n• {type(metric).__qualname__}:{log}")

    def enter_scope[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope not in self.scopes  # nosec: B101
        self.scopes[scope] = MetricsScopeStore(scope)

    def exit_scope[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None:
        assert scope in self.scopes  # nosec: B101
        self.scopes[scope].exited = monotonic()

        if __debug__:
            if scope.is_root and self.scopes[scope].finished:
                if log := _tree_log(
                    self.scopes[scope],
                    list_items_limit=self.items_limit,
                    item_character_limit=self.item_character_limit,
                ):
                    ctx.log_info(log)


def _tree_log(
    metrics: MetricsScopeStore,
    list_items_limit: int | None,
    item_character_limit: int | None,
) -> str:
    log: str = f"@{metrics.identifier}({metrics.time:.2f}s):"

    for metric in metrics.merged():
        metric_log: str = ""
        for key, value in vars(metric).items():
            if value_log := _value_log(
                value,
                list_items_limit=list_items_limit,
                item_character_limit=item_character_limit,
            ):
                metric_log += f"\n|  + {key}: {value_log}"

            else:
                continue  # skip missing values

        if not metric_log:
            continue  # skip empty logs

        log += f"\n• {type(metric).__qualname__}:{metric_log}"

    for nested in metrics.nested:
        nested_log: str = _tree_log(
            nested,
            list_items_limit=list_items_limit,
            item_character_limit=item_character_limit,
        ).replace("\n", "\n|  ")

        log += f"\n{nested_log}"

    return log.strip()


def _state_log(
    value: State,
    /,
    list_items_limit: int | None,
    item_character_limit: int | None,
) -> str | None:
    state_log: str = ""
    for key, element in vars(value).items():
        element_log: str | None = _value_log(
            element,
            list_items_limit=list_items_limit,
            item_character_limit=item_character_limit,
        )

        if element_log:
            state_log += f"\n|  + {key}: {element_log}"

        else:
            continue  # skip empty logs

    if state_log:
        return state_log.replace("\n", "\n|  ")

    else:
        return None  # skip empty logs


def _dict_log(
    value: dict[Any, Any],
    /,
    list_items_limit: int | None,
    item_character_limit: int | None,
) -> str | None:
    dict_log: str = ""
    for key, element in value.items():
        element_log: str | None = _value_log(
            element,
            list_items_limit=list_items_limit,
            item_character_limit=item_character_limit,
        )
        if element_log:
            dict_log += f"\n|  + {key}: {element_log}"

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
    item_character_limit: int | None,
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
            item_character_limit=item_character_limit,
        )
        if element_log:
            list_log += f"\n|  [{idx}] {element_log}"

        else:
            continue  # skip empty logs

    if list_log:
        return list_log.replace("\n", "\n|  ")

    else:
        return None  # skip empty logs


def _raw_value_log(
    value: Any,
    /,
    item_character_limit: int | None,
) -> str | None:
    if value is MISSING:
        return None  # skip missing

    value_log = str(value)
    if not value_log:
        return None  # skip empty logs

    if (item_character_limit := item_character_limit) and len(value_log) > item_character_limit:
        return value_log.replace("\n", " ")[:item_character_limit] + "..."

    else:
        return value_log.replace("\n", "\n|  ")


def _value_log(
    value: Any,
    /,
    list_items_limit: int | None,
    item_character_limit: int | None,
) -> str | None:
    # try unpack dicts
    if isinstance(value, dict):
        return _dict_log(
            cast(dict[Any, Any], value),
            list_items_limit=list_items_limit,
            item_character_limit=item_character_limit,
        )

    # try unpack lists
    elif isinstance(value, list):
        return _list_log(
            cast(list[Any], value),
            list_items_limit=list_items_limit,
            item_character_limit=item_character_limit,
        )

    # try unpack state
    elif isinstance(value, State):
        return _state_log(
            value,
            list_items_limit=list_items_limit,
            item_character_limit=item_character_limit,
        )

    else:
        return _raw_value_log(
            value,
            item_character_limit=item_character_limit,
        )
