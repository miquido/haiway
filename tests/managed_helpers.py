import asyncio
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence, Set
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, final
from uuid import UUID

from haiway import Observability, ObservabilityLevel, ObservabilityLink, State
from haiway.context import ContextIdentifier
from haiway.context.managed import (
    AttemptEnd,
    ClaimedInterrupted,
    ClaimedTask,
    InMemoryTaskStore,
    LeasedCoordination,
    LeaseRenewal,
    LeaseTimings,
    LocalCoordination,
    NewTask,
    StoreCoordination,
    StoredQueued,
    StoredStatus,
    StoredTask,
    TaskFilter,
    TaskKeyset,
    TaskStoreSession,
    TaskSummary,
)
from haiway.context.managed.memory import _Running
from haiway.context.managed.types import TaskCancellation


class Request(State):
    n: int = 0


class Output(State):
    n: int = 0


class Secret(State):
    value: str


@final
class Record:
    def __init__(
        self,
        scope: ContextIdentifier,
        level: ObservabilityLevel,
        message: str,
    ) -> None:
        self.scope: ContextIdentifier = scope
        self.level: ObservabilityLevel = level
        self.message: str = message


@final
class Entered:
    def __init__(
        self,
        scope: ContextIdentifier,
        links: Sequence[ObservabilityLink],
        trace: UUID,
    ) -> None:
        self.scope: ContextIdentifier = scope
        self.links: Sequence[ObservabilityLink] = tuple(links)
        self.trace: UUID = trace


@final
class Recording:
    """A backend recording every scope entered and every log made through it."""

    def __init__(self) -> None:
        self.records: list[Record] = []
        self.entered: list[Entered] = []
        self._traces: dict[UUID, UUID] = {}
        self.observability: Observability = Observability(
            trace_identifying=self._trace_identifying,
            log_recording=self._log_recording,
            event_recording=self._ignore,
            metric_recording=self._ignore,
            attributes_recording=self._ignore,
            scope_entering=self._scope_entering,
            scope_exiting=self._scope_exiting,
            trace_context_encoding=self._trace_context_encoding,
        )

    def _trace_identifying(self, scope: ContextIdentifier, /) -> UUID:
        return self._traces[scope.scope_id]

    def _log_recording(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        self.records.append(Record(scope, level, message % args if args else message))

    def _ignore(self, scope: ContextIdentifier, /, level: ObservabilityLevel, *_: Any, **__: Any):
        pass

    def _scope_entering(
        self,
        scope: ContextIdentifier,
        /,
        *,
        links: Sequence[ObservabilityLink],
    ) -> str:
        trace: UUID
        if scope.is_root or links or scope.parent_id not in self._traces:
            trace = UUID(int=scope.scope_id.int ^ 0xFFFF)

        else:
            trace = self._traces[scope.parent_id]

        self._traces[scope.scope_id] = trace
        self.entered.append(Entered(scope, links, trace))
        return trace.hex

    def _scope_exiting(
        self,
        scope: ContextIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        pass

    def _trace_context_encoding(self, scope: ContextIdentifier, /) -> Mapping[str, str]:
        return {}

    def messages(
        self,
        *,
        level: ObservabilityLevel | None = None,
        scope: str | None = None,
        containing: str | None = None,
    ) -> list[str]:
        return [
            record.message
            for record in self.records
            if (level is None or record.level == level)
            and (scope is None or record.scope.name == scope)
            and (containing is None or containing in record.message)
        ]

    def scopes(
        self,
        name: str,
    ) -> list[Entered]:
        return [entered for entered in self.entered if entered.scope.name == name]


async def _never_changed(
    seen: int,
    /,
) -> int:
    raise AssertionError("the coordinator never waits for the changes of a store")


def coordination_of(timings: LeaseTimings | None) -> StoreCoordination:
    if timings is None:
        return LocalCoordination(_never_changed)

    return LeasedCoordination(timings=timings)


async def eventually(
    condition: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    deadline: float = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition did not hold in time")

        await asyncio.sleep(0.005)


@asynccontextmanager
async def holding(
    store: InMemoryTaskStore,
) -> AsyncGenerator[TaskStoreSession]:
    """A session of a store opened beside the runtime, to look at what the store holds."""
    async with store.open() as session:
        yield session


@final
class Gate:
    """
    Holds one call of a store until the test releases it.

    A gate placed ``after`` the call lets the store do its work first and holds the reply,
    which is how a call commits and its reply arrives late. A gate placed before the call holds
    the work itself.
    """

    def __init__(
        self,
        *,
        after: bool,
    ) -> None:
        self.after: bool = after
        self.arrived: asyncio.Event = asyncio.Event()
        self._released: asyncio.Event = asyncio.Event()

    async def hold(self) -> None:
        self.arrived.set()
        await self._released.wait()

    def release(self) -> None:
        self._released.set()


@final
class LeasedStore:
    """
    In-process test double of a store with lease timings, on top of ``InMemoryTaskStore``.

    Yields ``LeasedCoordination`` and counts the calls it receives. Failures of ``record``
    and ``claim`` and delays of every call can be injected, a ``Gate`` holds the next call of
    a kind, a lease can be handed to another runner, and a lease can be made to expire for
    this store, which then claims it as a store with leases does.
    """

    def __init__(
        self,
        inner: InMemoryTaskStore | None = None,
        *,
        timings: LeaseTimings = LeaseTimings(lease=1.0, renewal=0.3, polling=0.1),  # noqa: B008
    ) -> None:
        self.inner: InMemoryTaskStore = inner if inner is not None else InMemoryTaskStore()
        self.timings: LeaseTimings = timings
        self.calls: Counter[str] = Counter()
        self.reads: list[tuple[float, Set[UUID]]] = []
        self.renewals: list[Set[UUID]] = []
        # the names each call failing unsupported tasks was given
        self.unsupported_names: list[Set[str]] = []
        self.record_failures: int = 0
        self.record_error_after_commit: int = 0
        self.record_delay: float = 0.0
        self.claim_failures: int = 0
        self.fail_unsupported_failures: int = 0
        self.query_failures: int = 0
        self.read_delay: float = 0.0
        self.claim_delay: float = 0.0
        # as a store with leases does, which leaves the task queued instead of cancelling it
        self.release_leaves_queued: bool = False
        self.closed: bool = False
        self.recorded: list[AttemptEnd] = []
        # the attempt numbers of the records which the store applied, in order
        self.committed: list[int] = []
        # the next call of a kind waits at its gate, the gate is used once
        self.claim_gate: Gate | None = None
        self.read_gate: Gate | None = None
        self.record_gate: Gate | None = None
        self.renew_gate: Gate | None = None
        self.query_gate: Gate | None = None
        # set when a claim committed at least one task
        self.claim_committed: asyncio.Event = asyncio.Event()
        # set when a read returned its result
        self.read_returned: asyncio.Event = asyncio.Event()
        # the runner of this store, known from its first call
        self.runner: UUID | None = None
        # the tasks whose lease this store claims as expired at its next claim
        self.expired: set[UUID] = set()
        self.interruptions: list[tuple[UUID, int]] = []

    def expire(
        self,
        identifier: UUID,
    ) -> None:
        """Make the lease of a running task expired, for the next claim of this store."""
        self.expired.add(identifier)

    def hand_lease_to(
        self,
        identifier: UUID,
        runner: UUID,
    ) -> None:
        """Give the lease of a running task to another runner, whose record this store applies."""
        entry = self.inner._tasks[identifier]
        state = entry.state
        assert isinstance(state, _Running)
        state.owner = runner

    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]:
        return self._open()

    @asynccontextmanager
    async def _open(self) -> AsyncGenerator[TaskStoreSession]:
        async with self.inner.open() as real:
            session = _LeasedSession(self, real)
            try:
                yield TaskStoreSession(
                    insert=session.insert,
                    claim=session.claim,
                    renew=session.renew,
                    record=session.record,
                    release=session.release,
                    cancel=session.cancel,
                    read=session.read,
                    fail_unsupported=session.fail_unsupported,
                    query=session.query,
                    coordination=LeasedCoordination(timings=self.timings),
                )

            finally:
                self.closed = True


@final
class _LeasedSession:
    def __init__(
        self,
        store: LeasedStore,
        real: TaskStoreSession,
    ) -> None:
        self._store: LeasedStore = store
        self._real: TaskStoreSession = real

    async def insert(self, task: NewTask, /) -> None:
        self._store.calls["insert"] += 1
        await self._real.insert(task)

    async def _interrupted(
        self,
        runner: UUID,
        names: Set[str],
    ) -> list[ClaimedTask]:
        # the leases which expired are claimed by the runner, whose attempt they interrupt,
        # for the names it lists only
        claimed: list[ClaimedTask] = []
        entries = self._store.inner._tasks
        for identifier in tuple(self._store.expired):
            entry = entries.get(identifier)
            if (
                entry is None
                or not isinstance(entry.state, _Running)
                or entry.task.name not in names
            ):
                continue

            self._store.expired.discard(identifier)
            entry.state.owner = runner
            attempt: int = len(entry.attempts) + 1
            self._store.interruptions.append((identifier, attempt))
            claimed.append(
                ClaimedInterrupted(
                    task=entry.task,
                    attempt=attempt,
                    cancel_requested=entry.state.cancel_requested,
                )
            )

        return claimed

    async def claim(
        self,
        runner: UUID,
        names: Set[str],
        *,
        limit: int | None,
    ) -> Sequence[ClaimedTask]:
        store = self._store
        store.runner = runner
        store.calls["claim"] += 1
        if store.claim_delay:
            await asyncio.sleep(store.claim_delay)

        if store.claim_failures > 0:
            store.claim_failures -= 1
            raise OSError("claim failed")

        claimed: list[ClaimedTask] = await self._interrupted(runner, names)
        claimed.extend(await self._real.claim(runner, names, limit=limit))
        if claimed:
            store.claim_committed.set()
            if store.claim_gate is not None:
                gate, store.claim_gate = store.claim_gate, None
                await gate.hold()

        return claimed

    async def fail_unsupported(
        self,
        names: Set[str],
        /,
    ) -> Mapping[UUID, str]:
        store = self._store
        store.calls["fail_unsupported"] += 1
        store.unsupported_names.append(frozenset(names))
        if store.fail_unsupported_failures > 0:
            store.fail_unsupported_failures -= 1
            raise OSError("fail_unsupported failed")

        return await self._real.fail_unsupported(names)

    async def query(
        self,
        matching: TaskFilter,
        /,
        *,
        after: TaskKeyset | None,
        limit: int,
    ) -> Sequence[TaskSummary]:
        store = self._store
        store.calls["query"] += 1
        if store.query_gate is not None:
            gate, store.query_gate = store.query_gate, None
            await gate.hold()

        if store.query_failures > 0:
            store.query_failures -= 1
            raise OSError("query failed")

        return await self._real.query(matching, after=after, limit=limit)

    async def renew(
        self,
        runner: UUID,
        identifiers: Set[UUID],
    ) -> LeaseRenewal:
        store = self._store
        store.runner = runner
        store.calls["renew"] += 1
        store.renewals.append(frozenset(identifiers))
        renewal = await self._real.renew(runner, identifiers)
        if store.renew_gate is not None:
            gate, store.renew_gate = store.renew_gate, None
            await gate.hold()

        return renewal

    async def record(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        end: AttemptEnd,
        /,
    ) -> StoredStatus | None:
        store = self._store
        store.runner = runner
        store.calls["record"] += 1
        gate: Gate | None = None
        if store.record_gate is not None:
            gate, store.record_gate = store.record_gate, None
            if not gate.after:
                await gate.hold()

        if store.record_delay:
            await asyncio.sleep(store.record_delay)

        if store.record_failures > 0:
            store.record_failures -= 1
            raise OSError("record failed")

        status = await self._real.record(runner, identifier, attempt, end)
        if status is not None:
            store.recorded.append(end)
            store.committed.append(attempt)

        if gate is not None and gate.after:
            await gate.hold()

        if store.record_error_after_commit > 0:
            store.record_error_after_commit -= 1
            raise OSError("reply of record lost")

        return status

    async def release(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        /,
    ) -> StoredStatus | None:
        self._store.runner = runner
        self._store.calls["release"] += 1
        if self._store.release_leaves_queued:
            return StoredQueued()

        return await self._real.release(runner, identifier, attempt)

    async def cancel(self, identifier: UUID, /) -> TaskCancellation | None:
        self._store.calls["cancel"] += 1
        return await self._real.cancel(identifier)

    async def read(self, identifiers: Set[UUID], /) -> Mapping[UUID, StoredTask]:
        store = self._store
        store.calls["read"] += 1
        store.reads.append((asyncio.get_running_loop().time(), frozenset(identifiers)))
        gate: Gate | None = None
        if store.read_gate is not None:
            gate, store.read_gate = store.read_gate, None
            if not gate.after:
                await gate.hold()

        if store.read_delay:
            await asyncio.sleep(store.read_delay)

        stored = await self._real.read(identifiers)
        if gate is not None and gate.after:
            await gate.hold()

        store.read_returned.set()
        return stored


@final
class CountingStore:
    """``InMemoryTaskStore`` counting the waits of runtimes for its changes."""

    def __init__(
        self,
        inner: InMemoryTaskStore | None = None,
    ) -> None:
        self.inner: InMemoryTaskStore = inner if inner is not None else InMemoryTaskStore()
        self.waits: int = 0
        self.claim_delay: float = 0.0
        self.failing_waits: int = 0
        self.claim_failures: int = 0
        self.cancelled_waits: int = 0
        self.on_claimed: Callable[[], None] | None = None
        self.unsupported_calls: int = 0
        self.queries: int = 0

    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]:
        return self._open()

    @asynccontextmanager
    async def _open(self) -> AsyncGenerator[TaskStoreSession]:
        async with self.inner.open() as real:
            coordination = real.coordination
            assert isinstance(coordination, LocalCoordination)

            async def changed(seen: int, /) -> int:
                self.waits += 1
                if self.failing_waits > 0:
                    self.failing_waits -= 1
                    raise OSError("waiting for changes failed")

                if self.cancelled_waits > 0:
                    self.cancelled_waits -= 1
                    future = asyncio.get_running_loop().create_future()
                    future.cancel()
                    await future

                return await coordination.changed(seen)

            async def claim(
                runner: UUID,
                names: Set[str],
                *,
                limit: int | None,
            ) -> Sequence[ClaimedTask]:
                if self.claim_delay:
                    await asyncio.sleep(self.claim_delay)

                if self.claim_failures > 0:
                    self.claim_failures -= 1
                    raise OSError("claim failed")

                claimed = await real.claim(runner, names, limit=limit)
                if claimed and self.on_claimed is not None:
                    self.on_claimed()

                return claimed

            async def fail_unsupported(names: Set[str], /) -> Mapping[UUID, str]:
                self.unsupported_calls += 1
                return await real.fail_unsupported(names)

            async def query(
                matching: TaskFilter,
                /,
                *,
                after: TaskKeyset | None,
                limit: int,
            ) -> Sequence[TaskSummary]:
                self.queries += 1
                return await real.query(matching, after=after, limit=limit)

            yield TaskStoreSession(
                insert=real.insert,
                claim=claim,
                renew=real.renew,
                record=real.record,
                release=real.release,
                cancel=real.cancel,
                read=real.read,
                fail_unsupported=fail_unsupported,
                query=query,
                coordination=LocalCoordination(changed),
            )


async def wait_for(
    awaitable: Awaitable[Any],
    timeout: float = 2.0,
) -> Any:
    async with asyncio.timeout(timeout):
        return await awaitable


@final
class Counts:
    def __init__(self) -> None:
        self.entered: int = 0
        self.exited: int = 0


def counting_disposable(
    counts: Counts,
    *state: State,
    fail_enter: int = 0,
    fail_exit: bool = False,
) -> Callable[[], Any]:
    """A preset disposable factory counting its entries and exits."""
    failures: list[int] = [fail_enter]

    def factory() -> Any:
        @asynccontextmanager
        async def disposable() -> AsyncGenerator[Sequence[State]]:
            counts.entered += 1
            if failures[0] > 0:
                failures[0] -= 1
                raise OSError("preparing failed")

            try:
                yield state

            finally:
                counts.exited += 1
                if fail_exit:
                    raise OSError("releasing failed")

        return disposable()

    return factory
