from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from enum import IntEnum
from logging import DEBUG as DEBUG_LOGGING
from logging import ERROR as ERROR_LOGGING
from logging import INFO as INFO_LOGGING
from logging import WARNING as WARNING_LOGGING
from logging import Logger, getLogger
from time import monotonic
from types import TracebackType
from typing import (
    Any,
    ClassVar,
    Final,
    Literal,
    NoReturn,
    Protocol,
    Self,
    final,
    runtime_checkable,
)
from uuid import UUID, uuid4

from haiway.context.identifier import ContextIdentifier
from haiway.context.types import ContextMissing
from haiway.types import Missing
from haiway.utils.formatting import escape_controls, format_log_message, format_str

__all__ = (
    "ContextObservability",
    "LoggerObservability",
    "Observability",
    "ObservabilityAttribute",
    "ObservabilityAttributesRecording",
    "ObservabilityEventRecording",
    "ObservabilityLevel",
    "ObservabilityLogRecording",
    "ObservabilityMetricKind",
    "ObservabilityMetricRecording",
    "ObservabilityScopeEntering",
    "ObservabilityScopeExiting",
    "ObservabilityTraceContextEncoding",
    "ObservabilityTraceIdentifying",
)


class ObservabilityLevel(IntEnum):
    # values from logging package
    ERROR = ERROR_LOGGING
    WARNING = WARNING_LOGGING
    INFO = INFO_LOGGING
    DEBUG = DEBUG_LOGGING


type ObservabilityAttribute = (
    Sequence[str]
    | Sequence[float]
    | Sequence[int]
    | Sequence[bool]
    | str
    | float
    | int
    | bool
    | Missing
    | None
)


@runtime_checkable
class ObservabilityTraceIdentifying(Protocol):
    """Resolve the trace the given scope belongs to.

    Implementations return one identifier per scope tree - the value a root
    creates and every scope nested below it inherits - so records made anywhere
    within a tree correlate, and concurrent trees stay apart. A scope the backend
    does not track has no trace to report and yields the all zero identifier.
    """

    def __call__(
        self,
        scope: ContextIdentifier,
        /,
    ) -> UUID: ...


@runtime_checkable
class ObservabilityTraceContextEncoding(Protocol):
    """Encode the current trace position for propagation to another service.

    Implementations return the carrier entries identifying the given scope
    within its trace - W3C ``traceparent`` and ``tracestate`` for an
    OpenTelemetry backend - to be attached to an outgoing request. A backend
    which has no trace position to hand out returns an empty mapping.
    """

    def __call__(
        self,
        scope: ContextIdentifier,
        /,
    ) -> Mapping[str, str]: ...


# a backend with no trace to report yields this instead of failing to resolve
# one - it renders as the all zero identifier tracing backends use for "none"
_NO_TRACE_ID: Final[UUID] = UUID(int=0)


def _no_trace_context(
    scope: ContextIdentifier,
    /,
) -> Mapping[str, str]:
    """Trace context of a backend which can not propagate one - always empty."""
    return {}


@runtime_checkable
class ObservabilityLogRecording(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None: ...


@runtime_checkable
class ObservabilityEventRecording(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


type ObservabilityMetricKind = Literal["counter", "histogram", "gauge"]


@runtime_checkable
class ObservabilityMetricRecording(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


@runtime_checkable
class ObservabilityAttributesRecording(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


@runtime_checkable
class ObservabilityScopeEntering(Protocol):
    """Begin recording the given scope and report the trace it belongs to.

    The returned value is what ``ctx.scope(...)`` yields when entered, and has to
    match what ``ObservabilityTraceIdentifying`` resolves within the same scope -
    unpadded lowercase hex of that identifier.
    """

    def __call__(
        self,
        scope: ContextIdentifier,
        /,
    ) -> str: ...


@runtime_checkable
class ObservabilityScopeExiting(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None: ...


@final  # immutable
class Observability:  # avoiding State inheritance to prevent propagation as scope state
    __slots__ = (
        "attributes_recording",
        "event_recording",
        "log_recording",
        "metric_recording",
        "scope_entering",
        "scope_exiting",
        "trace_context_encoding",
        "trace_identifying",
    )

    def __init__(
        self,
        trace_identifying: ObservabilityTraceIdentifying,
        log_recording: ObservabilityLogRecording,
        metric_recording: ObservabilityMetricRecording,
        event_recording: ObservabilityEventRecording,
        attributes_recording: ObservabilityAttributesRecording,
        scope_entering: ObservabilityScopeEntering,
        scope_exiting: ObservabilityScopeExiting,
        # optional - a backend with no trace position to hand out propagates nothing
        trace_context_encoding: ObservabilityTraceContextEncoding | None = None,
    ) -> None:
        self.trace_identifying: ObservabilityTraceIdentifying
        assert isinstance(trace_identifying, ObservabilityTraceIdentifying)  # nosec: B101
        object.__setattr__(
            self,
            "trace_identifying",
            trace_identifying,
        )
        self.log_recording: ObservabilityLogRecording
        assert isinstance(log_recording, ObservabilityLogRecording)  # nosec: B101
        object.__setattr__(
            self,
            "log_recording",
            log_recording,
        )
        self.metric_recording: ObservabilityMetricRecording
        assert isinstance(metric_recording, ObservabilityMetricRecording)  # nosec: B101
        object.__setattr__(
            self,
            "metric_recording",
            metric_recording,
        )
        self.event_recording: ObservabilityEventRecording
        assert isinstance(event_recording, ObservabilityEventRecording)  # nosec: B101
        object.__setattr__(
            self,
            "event_recording",
            event_recording,
        )
        self.attributes_recording: ObservabilityAttributesRecording
        assert isinstance(attributes_recording, ObservabilityAttributesRecording)  # nosec: B101
        object.__setattr__(
            self,
            "attributes_recording",
            attributes_recording,
        )
        self.scope_entering: ObservabilityScopeEntering
        assert isinstance(scope_entering, ObservabilityScopeEntering)  # nosec: B101
        object.__setattr__(
            self,
            "scope_entering",
            scope_entering,
        )
        self.scope_exiting: ObservabilityScopeExiting
        assert isinstance(scope_exiting, ObservabilityScopeExiting)  # nosec: B101
        object.__setattr__(
            self,
            "scope_exiting",
            scope_exiting,
        )
        self.trace_context_encoding: ObservabilityTraceContextEncoding
        assert trace_context_encoding is None or isinstance(  # nosec: B101
            trace_context_encoding, ObservabilityTraceContextEncoding
        )
        object.__setattr__(
            self,
            "trace_context_encoding",
            trace_context_encoding if trace_context_encoding is not None else _no_trace_context,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be deleted"
        )


class ScopeStore:
    __slots__ = (
        "_completed",
        "_exited",
        "_prefix",
        "entered",
        "identifier",
        "logger",
        "nested",
        "pending",
        "store",
        "trace_hex",
        "trace_id",
    )

    def __init__(
        self,
        identifier: ContextIdentifier,
        /,
        trace_id: UUID,
        logger: Logger,
    ) -> None:
        self.identifier: ContextIdentifier = identifier
        # identifies the whole scope tree - nested scopes inherit the one created
        # by their root, so concurrent trees are told apart the same way they are
        # under a tracing backend
        self.trace_id: UUID = trace_id
        # unpadded hex is the form trace backends expect, and it is what every
        # record within the scope is prefixed with - render it once
        self.trace_hex: str = trace_id.hex
        self._prefix: str | None = None
        # resolved per tree, so concurrent roots keep their own logger
        self.logger: Logger = logger
        # only populated when a summary is going to be rendered - the tree is
        # what keeps completed scopes alive until their root reports it
        self.nested: list[ScopeStore] = []
        self.entered: float = monotonic()
        self._exited: float | None = None
        self._completed: float | None = None
        # nested scopes entered but not completed yet - the scope may only
        # complete once all of them did, which can happen much later when a
        # nested scope outlives this one. counting them keeps the completion
        # check constant time instead of walking the whole subtree
        self.pending: int = 0
        self.store: list[str] = []

    @property
    def prefix(self) -> str:
        # every record produced within the scope carries the same prefix, so it is
        # rendered once, and only when something is actually recorded
        prefix: str | None = self._prefix
        if prefix is None:
            prefix = f"[{self.trace_hex}] {self.identifier.unique_name}"
            self._prefix = prefix

        return prefix

    @property
    def time(self) -> float:
        return (self._completed or monotonic()) - self.entered

    def exit(self) -> None:
        assert self._exited is None  # nosec: B101
        self._exited = monotonic()

    def try_complete(self) -> bool:
        if self._exited is None:
            return False  # not exited, not elegible for completion yet

        if self._completed is not None:
            return False  # already completed

        if self.pending:
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
    Create an Observability implementation backed by a standard Python logger.

    This is the implementation Haiway falls back to when no observability was
    provided for a scope, and the one used when a ``Logger`` is provided instead
    of an ``Observability``.

    Parameters
    ----------
    logger: Logger | None
        The logger to use for recording observability data. If None, a logger is
        created based on the name of each root scope entered.
    debug_context: bool
        Whether to retain each scope tree and render a hierarchical summary of it
        when its root completes. Defaults to True in debug mode (__debug__) and
        False otherwise. Turning it off also stops the tree from being retained,
        leaving only the scopes which are still live tracked.

    Returns
    -------
    Observability
        An Observability implementation writing through the given logger.

    Notes
    -----
    Scope lifecycle - entering, exiting and the resulting duration - is recorded
    at DEBUG, since it describes the shape of the execution rather than what the
    application did. Logs, events, metrics and attributes are recorded at the
    level their call site asked for. A scope exiting with a regular exception is
    reported at ERROR; cancellation is not, being routine control flow under
    structured concurrency.

    A single instance may back several independent scope trees, including
    concurrent ones, as long as they run on one event loop. Each tree gets its
    own trace identifier, and is summarized and released on its own. Sharing one
    instance across threads is not supported - create one per loop instead.

    A scope completes only once every scope nested below it completed, which
    keeps parent-child lifetimes intact regardless of the order they finish in.
    The duration reported is the one of the scope itself, not of its longest
    descendant.
    """
    # keyed by the raw integer of the scope UUID - hashing an int is done in C,
    # while hashing a UUID goes through a Python level `__hash__` on every lookup
    scopes: dict[int, ScopeStore] = {}

    def trace_identifying(
        scope: ContextIdentifier,
        /,
    ) -> UUID:
        store: ScopeStore | None = scopes.get(scope.scope_id.int)
        if store is None:
            # an untracked scope belongs to no tree known here - reporting the
            # zero identifier keeps resolving one from failing, the same way
            # recording within such a scope is skipped instead of raising
            return _NO_TRACE_ID

        return store.trace_id

    def log_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        store: ScopeStore | None = scopes.get(scope.scope_id.int)
        if store is None:
            return  # skip without store

        if not store.logger.isEnabledFor(level):
            return  # skip formatting when the record would be discarded

        # the message is interpolated and escaped before the prefix is added, so
        # neither its arguments nor its content can alter the resulting record
        store.logger.log(
            level,
            f"{store.prefix} {format_log_message(message, args)}",
            exc_info=exception,
        )

    def event_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        store: ScopeStore | None = scopes.get(scope.scope_id.int)
        if store is None:
            return  # skip without store

        if not debug_context and not store.logger.isEnabledFor(level):
            return  # nothing to summarize and nothing to write - skip formatting

        event_str: str = f"Event: {escape_controls(event)} {format_str(attributes)}"
        if debug_context:  # store only for summary
            store.store.append(event_str)

        store.logger.log(
            level,
            f"{store.prefix} {event_str}",
        )

    def metric_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        store: ScopeStore | None = scopes.get(scope.scope_id.int)
        if store is None:
            return  # skip without store

        if not debug_context and not store.logger.isEnabledFor(level):
            return  # nothing to summarize and nothing to write - skip formatting

        metric_name: str = escape_controls(metric)
        metric_unit: str = escape_controls(unit) if unit else ""
        metric_str: str
        if attributes:
            metric_str = f"Metric: {metric_name} = {value} {metric_unit}\n{format_str(attributes)}"

        else:
            metric_str = f"Metric: {metric_name} = {value} {metric_unit}"

        if debug_context:  # store only for summary
            store.store.append(metric_str)

        store.logger.log(
            level,
            f"{store.prefix} {metric_str}",
        )

    def attributes_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        if not attributes:
            return  # skip empty

        store: ScopeStore | None = scopes.get(scope.scope_id.int)
        if store is None:
            return  # skip without store

        if not debug_context and not store.logger.isEnabledFor(level):
            return  # nothing to summarize and nothing to write - skip formatting

        attributes_str: str = f"Attributes: {format_str(attributes)}"
        if debug_context:  # store only for summary
            store.store.append(attributes_str)

        store.logger.log(
            level,
            f"{store.prefix} {attributes_str}",
        )

    def scope_entering(
        scope: ContextIdentifier,
        /,
    ) -> str:
        assert scope.scope_id.int not in scopes  # nosec: B101
        # a root scope is its own parent, so the lookup misses and it starts a
        # tree of its own. it also misses for a nested scope entered after the
        # scope it belongs to already completed
        parent: ScopeStore | None = scopes.get(scope.parent_id.int)
        store: ScopeStore = ScopeStore(
            scope,
            # one trace per tree - a root starts it, everything below inherits it
            trace_id=parent.trace_id if parent is not None else uuid4(),
            logger=parent.logger if parent is not None else logger or getLogger(scope.name),
        )
        if parent is not None:
            if debug_context:  # retain the tree only to summarize it
                parent.nested.append(store)

            parent.pending += 1

        scopes[scope.scope_id.int] = store
        if store.logger.isEnabledFor(ObservabilityLevel.DEBUG):
            store.logger.log(
                ObservabilityLevel.DEBUG,
                f"{store.prefix} Entering scope: {scope.name}",
            )

        return store.trace_hex

    def record_completion(
        store: ScopeStore,
        /,
    ) -> None:
        debug_enabled: bool = store.logger.isEnabledFor(ObservabilityLevel.DEBUG)
        if not debug_context and not debug_enabled:
            return  # nothing to summarize and nothing to write - skip formatting

        if debug_enabled:
            store.logger.log(
                ObservabilityLevel.DEBUG,
                f"{store.prefix} Exiting scope: {store.identifier.name}",
            )

        metric_str: str = f"Metric - scope_time:{store.time:.3f}s"
        if debug_context:  # store only for summary
            store.store.append(metric_str)

        if debug_enabled:
            store.logger.log(
                ObservabilityLevel.DEBUG,
                f"{store.prefix} {metric_str}",
            )

    def scope_exiting(
        scope: ContextIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        store: ScopeStore | None = scopes.get(scope.scope_id.int)
        if store is None:
            return  # skip without store

        store.exit()
        # only regular exceptions are failures - cancellation is routine control
        # flow under structured concurrency and reporting it would bury the rest
        if isinstance(exception, Exception):
            error_str: str = f"Scope error: {escape_controls(str(exception))}"
            if debug_context:  # store only for summary
                store.store.append(error_str)

            store.logger.log(
                ObservabilityLevel.ERROR,
                f"{store.prefix} {error_str}",
                exc_info=exception,
            )

        # complete the scope and every ancestor which was waiting for it
        while store.try_complete():
            identifier: ContextIdentifier = store.identifier
            record_completion(store)

            # a root scope is its own parent, so the lookup finds itself
            parent: ScopeStore | None = scopes.get(identifier.parent_id.int)
            # a completed scope is never recorded into again - unlink it here,
            # the summary reaches it through the tree its root retained
            del scopes[identifier.scope_id.int]
            if parent is None or parent is store:
                if debug_context:
                    store.logger.log(
                        ObservabilityLevel.DEBUG,
                        f"Observability summary:\n{_tree_summary(store)}",
                    )

                break

            parent.pending -= 1
            store = parent

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
    """Render a scope and everything nested below it as an indented tree."""
    elements: list[str] = [f"┍━ {scope_store.identifier.name} [{scope_store.identifier.scope_id}]:"]
    for element in scope_store.store:
        if not element:
            continue  # skip empty

        elements.append(f"┝ {element.replace('\n', '\n|  ')}")

    for nested in scope_store.nested:
        nested_summary: str = _tree_summary(nested)

        elements.append(f"|  {nested_summary.replace('\n', '\n|  ')}")

    return "\n".join(elements) + "\n┕━"


@final  # consider immutable
class ContextObservability:
    @classmethod
    def scope(
        cls,
        scope: ContextIdentifier,
        /,
        *,
        observability: Observability | Logger | None,
    ) -> Self:
        if observability is None:
            try:  # check for current scope
                return cls(
                    scope=scope,
                    observability=cls._context.get().observability,
                )

            except LookupError:  # create default logger observability on missing
                return cls(
                    scope=scope,
                    observability=LoggerObservability(
                        getLogger(scope.name),
                        debug_context=False,
                    ),
                )

        elif isinstance(observability, Logger):
            return cls(
                scope=scope,
                observability=LoggerObservability(
                    observability,
                    debug_context=False,
                ),
            )

        else:
            return cls(
                scope=scope,
                observability=observability,
            )

    @classmethod
    def trace_id(cls) -> str:
        context: Self
        try:  # resolve the context separately - LookupError raised by the
            # observability implementation itself is not a missing context
            context = cls._context.get()

        except LookupError:
            raise ContextMissing("Context observability requested but not defined!") from None

        try:  # catch exceptions - we don't want to blow up on observability
            # unpadded hex rather than the dashed UUID form - it is what a trace
            # backend displays and queries by, so the value pastes straight into
            # one
            return context.observability.trace_identifying(ContextIdentifier.current()).hex

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                "Failed to resolve trace identifier",
                exception=exc,
            )
            # the zero identifier is the contracted value for a trace which can
            # not be reported - resolving one never fails a caller
            return _NO_TRACE_ID.hex

    @classmethod
    def trace_context(cls) -> Mapping[str, str]:
        """Encode the current trace position for propagation to another service.

        Returns
        -------
        Mapping[str, str]
            Carrier entries identifying the current scope within its trace, to
            be attached to an outgoing request. Empty when there is no context,
            when the observability backend can not encode one, or when encoding
            failed - propagation is best effort and never fails a request.
        """
        context: Self
        try:  # resolve the context separately - LookupError raised by the
            # observability implementation itself is not a missing context
            context = cls._context.get()

        except LookupError:
            return {}  # no observability out of context - nothing to propagate

        try:  # catch exceptions - we don't want to blow up on observability
            return context.observability.trace_context_encoding(context._scope)

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                "Failed to encode trace context",
                exception=exc,
            )
            return {}

    @classmethod
    def record_log(
        cls,
        level: ObservabilityLevel,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        context: Self
        try:  # resolve the context separately - LookupError raised by the
            # observability implementation itself is not a missing context
            context = cls._context.get()

        except LookupError:  # fallback for access out of context
            return getLogger().log(
                level,
                format_log_message(message, args),
                exc_info=exception,
            )

        try:
            context.observability.log_recording(
                context._scope,
                level,
                message,
                *args,
                exception=exception,
            )

        # catch exceptions - we don't want to blow up on observability
        except Exception as exc:
            logger: Logger = getLogger()
            logger.log(
                ObservabilityLevel.ERROR,
                "Failed to log a message within observability system",
                exc_info=exc,
            )
            logger.log(
                level,
                format_log_message(message, args),
                exc_info=exception,
            )

    @classmethod
    def record_event(
        cls,
        level: ObservabilityLevel,
        event: str,
        /,
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        context: Self
        try:  # resolve the context separately - LookupError raised by the
            # observability implementation itself is not a missing context
            context = cls._context.get()

        except LookupError:
            return  # no observability out of context - nothing to record within

        try:  # catch exceptions - we don't want to blow up on observability
            context.observability.event_recording(
                context._scope,
                level=level,
                event=event,
                attributes=attributes,
            )

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                f"Failed to record event: {event}",
                exception=exc,
            )

    @classmethod
    def record_metric(
        cls,
        level: ObservabilityLevel,
        metric: str,
        /,
        *,
        value: float | int,
        unit: str | None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        context: Self
        try:  # resolve the context separately - LookupError raised by the
            # observability implementation itself is not a missing context
            context = cls._context.get()

        except LookupError:
            return  # no observability out of context - nothing to record within

        try:  # catch exceptions - we don't want to blow up on observability
            context.observability.metric_recording(
                context._scope,
                level=level,
                metric=metric,
                value=value,
                unit=unit,
                kind=kind,
                attributes=attributes,
            )

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                f"Failed to record metric: {metric}",
                exception=exc,
            )

    @classmethod
    def record_attributes(
        cls,
        level: ObservabilityLevel,
        /,
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        context: Self
        try:  # resolve the context separately - LookupError raised by the
            # observability implementation itself is not a missing context
            context = cls._context.get()

        except LookupError:
            return  # no observability out of context - nothing to record within

        try:  # catch exceptions - we don't want to blow up on observability
            context.observability.attributes_recording(
                context._scope,
                level=level,
                attributes=attributes,
            )

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                "Failed to record attributes",
                exception=exc,
            )

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextObservability")
    __slots__ = (
        "_scope",
        "_token",
        "observability",
    )

    def __init__(
        self,
        scope: ContextIdentifier,
        observability: Observability,
    ) -> None:
        self.observability: Observability = observability
        self._scope: ContextIdentifier = scope
        self._token: Token[ContextObservability] | None = None

    def __enter__(self) -> str:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        self._token = ContextObservability._context.set(self)
        try:
            return self.observability.scope_entering(self._scope)

        except BaseException:
            # __exit__ is never called when __enter__ raises, so the context
            # variable has to be restored here to avoid leaking this scope
            ContextObservability._context.reset(self._token)
            self._token = None
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ContextObservability._context.reset(self._token)
        self._token = None

        try:
            self.observability.scope_exiting(
                self._scope,
                exception=exc_val,
            )

        except Exception as exc:
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                "Failed to properly exit observability scope",
                exception=exc,
            )
