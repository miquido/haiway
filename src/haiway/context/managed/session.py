from asyncio import (
    FIRST_COMPLETED,
    CancelledError,
    Future,
    Queue,
    Task,
    TaskGroup,
    TimerHandle,
    current_task,
    get_running_loop,
    sleep,
    wait,
)
from collections.abc import AsyncGenerator, Callable, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import Context, copy_context
from functools import partial
from typing import Literal, Never, cast, final
from uuid import UUID, uuid4

from haiway.attributes import State
from haiway.context.disposables import Disposables
from haiway.context.identifier import random_identifier
from haiway.context.managed.attempt import run_attempt
from haiway.context.managed.coordinator import (
    AttemptEnded,
    CallCancelled,
    CancelAsked,
    CancelReplied,
    CancelTask,
    ChangedReplied,
    ChangesCancelled,
    Claim,
    ClaimReplied,
    Closed,
    CloseRequested,
    Coordinator,
    Effect,
    Event,
    ExitHost,
    Fail,
    FailUnsupported,
    Insert,
    InsertReplied,
    InterruptionRecorded,
    Log,
    Open,
    Opened,
    OutcomeAbandoned,
    OutcomeAsked,
    Read,
    ReadReplied,
    Record,
    RecordInterruption,
    RecordReplied,
    Release,
    ReleaseReplied,
    Renew,
    RenewReplied,
    Resolve,
    RunAttempt,
    ScopeEntered,
    ScopeExited,
    ScopeFailed,
    SpawnAsked,
    StartChanges,
    StartHost,
    StartTimer,
    StopChanges,
    StopTimer,
    StopTimers,
    StoreCallAsked,
    StoreCallEffect,
    StoreCallReplied,
    StoreFailure,
    StoreReply,
    TimerKey,
    UnsupportedReplied,
)
from haiway.context.managed.handle import ManagedTask, new_handle
from haiway.context.managed.listing import (
    TaskFilter,
    TaskKeyset,
    TaskSummary,
    decode_task_page_token,
    encode_task_page_token,
)
from haiway.context.managed.outcomes import exception_type_name
from haiway.context.managed.records import record_debug, record_error, record_warning
from haiway.context.managed.registration import RegisteredTask, RegisteredTaskCall
from haiway.context.managed.root import TaskRoot
from haiway.context.managed.state import ManagedTasks
from haiway.context.managed.store import (
    LocalCoordination,
    TaskStore,
    TaskStoreSession,
)
from haiway.context.managed.stored import NewTask, StoredTask
from haiway.context.managed.types import (
    TaskCancellation,
    TaskOutcome,
    TaskRetryPolicy,
    TaskStatus,
)
from haiway.context.observability import Observability, ObservabilityLink
from haiway.context.scope import ContextScope
from haiway.utils.pagination import Paginated, Pagination

__all__ = ("RuntimeSession",)

# the largest page a listing returns
_MAX_PAGE_SIZE: int = 1000


@final
class _FutureReply[T]:
    # what a caller waits for: the coordinator resolves it with the result of the event the
    # caller asked with, and a caller which stopped waiting leaves it cancelled
    __slots__ = ("future",)

    def __init__(
        self,
        future: Future[T],
        /,
    ) -> None:
        self.future: Future[T] = future

    def resolve(
        self,
        value: object,
        /,
    ) -> None:
        if not self.future.done():
            # the coordinator names no type of what it resolves: a caller asks with the event
            # which makes the coordinator resolve its reply with a value of that very type,
            # and a Resolve which carried the reply bound to its value would need every event
            # and the state of the coordinator generic over it
            self.future.set_result(cast(T, value))

    def fail(
        self,
        exception: Exception,
        /,
    ) -> None:
        if not self.future.done():
            self.future.set_exception(exception)


@final
class RuntimeSession:
    """
    One opened runtime: its store session, the coordinator of its runs and everything which
    serves them.

    Everything runs on the event loop the block was entered on. The main task of the session
    owns the runtime scope and the store session, from the opening to the close, in a context
    of its own, and it is the only one to read the events of the runtime: callers, hosts of
    task scopes, store calls and timers post events, the ``Coordinator`` is the only writer
    of the state of the runs, and the main task carries out the effects it returns.
    """

    __slots__ = (
        "_backend",
        "_changes",
        "_commands",
        "_coordinator",
        "_failure",
        "_grace_period",
        "_group",
        "_limit",
        "_loop",
        "_owner",
        "_queue",
        "_ready",
        "_replies",
        "_retry",
        "_root",
        "_runner",
        "_session",
        "_store",
        "_tasks",
        "_timers",
        "_unsupported",
    )

    def __init__(
        self,
        *,
        store: TaskStore,
        tasks: Mapping[str, RegisteredTask[Never, State | None]],
        retry: TaskRetryPolicy,
        grace_period: float,
        concurrency_limit: int | None,
        backend: Observability,
        root: TaskRoot,
        unsupported: Literal["leave", "fail"],
    ) -> None:
        self._store: TaskStore = store
        self._tasks: Mapping[str, RegisteredTask[Never, State | None]] = tasks
        self._retry: TaskRetryPolicy = retry
        self._grace_period: float = grace_period
        self._limit: int | None = concurrency_limit
        self._backend: Observability = backend
        self._root: TaskRoot = root
        self._unsupported: Literal["leave", "fail"] = unsupported
        # draws the identity of this runtime among the holders of leases
        self._runner: UUID = uuid4()
        self._loop = get_running_loop()
        self._queue: Queue[Event] = Queue()
        # known once the store session is opened
        self._session: TaskStoreSession | None = None
        self._coordinator: Coordinator | None = None
        self._owner: ManagedTasks | None = None
        self._group: TaskGroup | None = None
        # only ever completed with a result, the block waits for it together with the main task
        self._ready: Future[None] = self._loop.create_future()
        self._failure: Exception | None = None
        # the commands of the hosts of task scopes, by the serial of their run
        self._commands: dict[int, Queue[RunAttempt | ExitHost]] = {}
        self._changes: Task[int | StoreFailure] | None = None
        self._timers: dict[TimerKey, TimerHandle] = {}
        # what callers wait for and no reply reached yet, failed when the coordinator fails
        self._replies: set[_FutureReply[object]] = set()

    # --- opening and closing ---------------------------------------------------------

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[ManagedTasks]:
        main: Task[None] = self._loop.create_task(
            self._main(),
            context=Context(),
        )
        try:
            # the shared future is never awaited directly, cancelling this wait must not
            # cancel it for the main task
            await wait({self._ready, main}, return_when=FIRST_COMPLETED)

        except CancelledError:
            # the entry was cancelled, what the main task started is stopped before it goes on
            main.cancel()
            await self._finished(main)
            raise

        if not self._ready.done():
            await self._finished(main)
            if self._failure is not None:
                raise self._failure

            main.result()
            raise RuntimeError("ManagedTasks runtime failed to open")

        assert self._owner is not None  # nosec: B101
        try:
            yield self._owner

        finally:
            self._queue.put_nowait(CloseRequested())
            cancelled: CancelledError | None = await self._finished(main)
            failure: BaseException | None = None if main.cancelled() else main.exception()
            if cancelled is not None:
                if failure is not None:
                    # the cancellation of the exit is the one raised, what failed is not lost
                    record_error("Managed tasks close failed: %s", exception_type_name(failure))

                raise cancelled

            if failure is not None:
                raise failure

    async def _finished(
        self,
        main: Task[None],
        /,
    ) -> CancelledError | None:
        # the steps of a close are not abandoned, a cancellation follows them
        cancelled: CancelledError | None = None
        while not main.done():
            try:
                await wait({main})

            except CancelledError as exc:
                cancelled = exc

        return cancelled

    async def _main(self) -> None:
        try:
            async with ContextScope(
                name="managed_tasks",
                presets=None,
                state=(),
                disposables=Disposables.of(),
                observability=self._backend,
                kind="internal",
                isolated=False,
            ):
                async with self._store.open() as session:
                    self._session = session
                    coordination = session.coordination
                    self._coordinator = Coordinator(
                        results={name: task.result for name, task in self._tasks.items()},
                        limit=self._limit,
                        grace_period=self._grace_period,
                        coordination=coordination,
                        unsupported=self._unsupported,
                    )
                    self._owner = ManagedTasks(
                        availability=self._available,
                        spawning=self._spawn,
                        lookup=self._lookup,
                        listing=self._list,
                    )
                    try:
                        await self._serve(self._coordinator)

                    finally:
                        self._abort(self._coordinator)

        except Exception as exc:
            if self._ready.done():
                raise

            # a failed opening is raised by the block, nothing was entered
            self._failure = exc

    async def _serve(
        self,
        coordinator: Coordinator,
        /,
    ) -> None:
        cancelled: CancelledError | None = None
        # a coordinator which fails ends the group: everything it runs is cancelled and awaited
        async with TaskGroup() as group:
            self._group = group
            self._execute(coordinator.handle(Opened()))
            if not self._ready.done():
                self._ready.set_result(None)

            cancelled = await self._events(coordinator)

        if cancelled is not None:
            raise cancelled

    async def _events(
        self,
        coordinator: Coordinator,
        /,
    ) -> CancelledError | None:
        # the cancellation of the main task is a close, and is raised once it is done
        cancelled: CancelledError | None = None
        while not isinstance(coordinator.lifecycle, Closed):
            event: Event
            try:
                event = await self._queue.get()

            except CancelledError as exc:
                cancelled = exc
                task: Task[object] | None = current_task()
                if task is not None:
                    task.uncancel()

                event = CloseRequested()

            self._execute(coordinator.handle(event))

        # what was posted before the close ended is refused
        while not self._queue.empty():
            self._execute(coordinator.handle(self._queue.get_nowait()))

        try:
            # a future which the last step resolved wakes its caller one iteration of the loop
            # later: yielding once lets every caller a status, outcome or store call was
            # answered to return while the store session is still open (FR-29.6)
            await sleep(0)

        except CancelledError as exc:
            cancelled = cancelled or exc

        return cancelled

    def _abort(
        self,
        coordinator: Coordinator,
        /,
    ) -> None:
        for timer in self._timers.values():
            timer.cancel()

        self._timers.clear()
        if not isinstance(coordinator.lifecycle, Closed):
            # the coordinator did not end the runtime: what waits for it is failed
            self._execute(coordinator.abort())
            failure: Exception = RuntimeError("ManagedTasks runtime failed")
            for reply in tuple(self._replies):
                reply.fail(failure)

    # --- carrying out effects --------------------------------------------------------

    def _post(
        self,
        event: Event,
        /,
    ) -> None:
        self._queue.put_nowait(event)

    def _execute(
        self,
        effects: Sequence[Effect],
        /,
    ) -> None:
        for effect in effects:
            match effect:
                case (
                    Claim()
                    | FailUnsupported()
                    | Renew()
                    | Read()
                    | Record()
                    | RecordInterruption()
                    | Release()
                ):
                    self._store_effect(effect)

                case Insert() | CancelTask() | StoreCallEffect():
                    self._caller_effect(effect)

                case StartChanges() | StopChanges():
                    self._changes_effect(effect)

                case StartHost() | RunAttempt() | ExitHost():
                    self._host_effect(effect)

                case StartTimer() | StopTimer() | StopTimers():
                    self._timer_effect(effect)

                case Resolve(reply=reply, value=value):
                    reply.resolve(value)

                case Fail(reply=reply, exception=exception):
                    reply.fail(exception)

                case Log():
                    self._log(effect)

    def _log(
        self,
        effect: Log,
        /,
    ) -> None:
        record: Callable[..., None]
        match effect.level:
            case "debug":
                record = record_debug

            case "warning":
                record = record_warning

            case "error":
                record = record_error

        if effect.context is None:
            record(effect.message, *effect.arguments)

        else:
            effect.context.run(record, effect.message, *effect.arguments)

    def _store_session(self) -> TaskStoreSession:
        assert self._session is not None  # nosec: B101
        return self._session

    def _coordinating(self) -> Coordinator:
        assert self._coordinator is not None  # nosec: B101
        return self._coordinator

    def _task_group(self) -> TaskGroup:
        assert self._group is not None  # nosec: B101
        return self._group

    async def _guarded[T](
        self,
        call: Callable[[], Coroutine[None, None, T]],
        /,
    ) -> T | StoreFailure:
        # a task of the group which raises would end the group, so a failure is a reply. The
        # coroutine is made here, so that one cancelled before it started leaves none unawaited.
        try:
            return await call()

        except Exception as exc:
            return StoreFailure(exc)

        except CancelledError as exc:
            task: Task[object] | None = current_task()
            if task is not None and task.cancelling():
                raise  # cancelled by the runtime itself

            # a cancellation of something inside the call is a failure of the call, which
            # is answered like any: no call of the runtime is left without a reply
            failure: RuntimeError = RuntimeError("A call of the store was cancelled")
            failure.__cause__ = exc
            return StoreFailure(failure)

    def _replied[T](
        self,
        reply: Callable[[T | StoreFailure | CallCancelled], StoreReply],
        task: Task[T | StoreFailure],
        /,
    ) -> None:
        # from the done-callback of the call, never from inside its coroutine. A call which
        # ended cancelled, by the group of the session or the shutdown of the loop, is answered
        # too, or the runtime would wait for it forever
        if task.cancelled():
            self._post(reply(CallCancelled()))

        else:
            self._post(reply(task.result()))

    def _io[T](
        self,
        call: Callable[[], Coroutine[None, None, T]],
        reply: Callable[[T | StoreFailure | CallCancelled], StoreReply],
        context: Context | None,
        /,
    ) -> None:
        task: Task[T | StoreFailure] = self._task_group().create_task(
            self._guarded(call),
            context=context,
        )
        task.add_done_callback(partial(self._replied, reply))

    def _store_effect(
        self,
        effect: Claim | FailUnsupported | Renew | Read | Record | RecordInterruption | Release,
        /,
    ) -> None:
        session: TaskStoreSession = self._store_session()
        match effect:
            case Claim(limit=limit):
                self._io(
                    lambda: session.claim(self._runner, frozenset(self._tasks), limit=limit),
                    ClaimReplied,
                    None,
                )

            case FailUnsupported(names=names):
                self._io(
                    lambda: session.fail_unsupported(names),
                    UnsupportedReplied,
                    None,
                )

            case Renew(asked=asked, issued=issued):
                self._io(
                    lambda: session.renew(self._runner, frozenset(asked)),
                    lambda result: RenewReplied(asked, issued, result),
                    None,
                )

            case Read(identifiers=identifiers, parks=parks, epoch=epoch):
                self._io(
                    lambda: session.read(identifiers),
                    lambda result: ReadReplied(identifiers, parks, epoch, result),
                    None,
                )

            case Record(
                identifier=identifier,
                serial=serial,
                attempt=attempt,
                end=end,
                context=ctx,
            ):
                self._io(
                    lambda: session.record(self._runner, identifier, attempt, end),
                    lambda result: RecordReplied(identifier, serial, attempt, result),
                    None if ctx is None else ctx.copy(),
                )

            case RecordInterruption(identifier=identifier, attempt=attempt, end=end):
                self._io(
                    lambda: session.record(self._runner, identifier, attempt, end),
                    lambda result: InterruptionRecorded(identifier, attempt, result),
                    None,
                )

            case Release(identifier=identifier, serial=serial, attempt=attempt, context=ctx):
                self._io(
                    lambda: session.release(self._runner, identifier, attempt),
                    lambda result: ReleaseReplied(identifier, serial, attempt, result),
                    None if ctx is None else ctx.copy(),
                )

    def _caller_effect(
        self,
        effect: Insert | CancelTask | StoreCallEffect,
        /,
    ) -> None:
        # in the context of the caller, which made a copy of it for this call
        session: TaskStoreSession = self._store_session()
        match effect:
            case Insert(task=task, reply=reply, context=context):
                self._io(
                    lambda: session.insert(task),
                    lambda result: InsertReplied(reply, result),
                    context,
                )

            case CancelTask(identifier=identifier, reply=reply, context=context):
                self._io(
                    lambda: session.cancel(identifier),
                    lambda result: CancelReplied(identifier, reply, result),
                    context,
                )

            case StoreCallEffect(call=call, reply=reply, context=context):
                self._io(
                    call,
                    lambda result: StoreCallReplied(reply, result),
                    context,
                )

    def _changes_effect(
        self,
        effect: StartChanges | StopChanges,
        /,
    ) -> None:
        match effect:
            case StartChanges(seen=seen):
                coordination = self._store_session().coordination
                assert isinstance(coordination, LocalCoordination)  # nosec: B101
                task: Task[int | StoreFailure] = self._task_group().create_task(
                    self._guarded(lambda: coordination.changed(seen))
                )
                task.add_done_callback(self._changed)
                self._changes = task

            case StopChanges():
                # cancelled at once and awaited until it ends, not for its result
                if self._changes is not None:
                    self._changes.cancel()

    def _changed(
        self,
        task: Task[int | StoreFailure],
        /,
    ) -> None:
        self._post(ChangedReplied(ChangesCancelled() if task.cancelled() else task.result()))

    def _timer_effect(
        self,
        effect: StartTimer | StopTimer | StopTimers,
        /,
    ) -> None:
        match effect:
            case StartTimer(key=key, delay=delay, event=event):
                self._stop_timer(key)
                self._timers[key] = self._loop.call_later(delay, self._timer_fired, key, event)

            case StopTimer(key=key):
                self._stop_timer(key)

            case StopTimers(kind=kind):
                for key in [key for key in self._timers if key[0] == kind]:
                    self._stop_timer(key)

    def _stop_timer(
        self,
        key: TimerKey,
        /,
    ) -> None:
        timer: TimerHandle | None = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    def _timer_fired(
        self,
        key: TimerKey,
        event: Event,
        /,
    ) -> None:
        self._timers.pop(key, None)
        self._post(event)

    # --- hosts of task scopes --------------------------------------------------------

    def _host_effect(
        self,
        effect: StartHost | RunAttempt | ExitHost,
        /,
    ) -> None:
        match effect:
            case StartHost(task=task, serial=serial):
                assert self._owner is not None  # nosec: B101
                commands: Queue[RunAttempt | ExitHost] = Queue()
                self._commands[serial] = commands
                self._task_group().create_task(
                    self._host(task, serial, commands),
                    context=self._root.context(self._owner),
                )

            case RunAttempt(serial=serial) | ExitHost(serial=serial):
                host: Queue[RunAttempt | ExitHost] | None = self._commands.get(serial)
                if host is not None:
                    host.put_nowait(effect)

    async def _host(
        self,
        task: NewTask,
        serial: int,
        commands: Queue[RunAttempt | ExitHost],
        /,
    ) -> None:
        # serves the task scope of one run, for as long as the run lasts: an error of the
        # scope is a reply, only a cancellation or an interpreter exit leaves the task
        entered: bool = False
        try:
            async with self._root.task_scope(
                task.name,
                link=task.origin,
                observability=self._backend,
            ):
                entered = True
                self._post(ScopeEntered(task.identifier, serial, copy_context()))
                await self._commanded(task, serial, commands)

        except BaseException as exc:
            self._post(
                ScopeExited(task.identifier, serial, exc)
                if entered
                else ScopeFailed(task.identifier, serial, exc)
            )
            if isinstance(exc, CancelledError):
                raise

            if not isinstance(exc, Exception):
                # an interpreter exit, which asyncio raised from the task of the attempt and
                # stopped the loop with: raising it again would abort the shutdown which
                # follows, so the host ends by cancellation once the coordinator has it
                raise CancelledError from exc

        else:
            self._post(ScopeExited(task.identifier, serial, None))

        finally:
            self._commands.pop(serial, None)

    async def _commanded(
        self,
        task: NewTask,
        serial: int,
        commands: Queue[RunAttempt | ExitHost],
        /,
    ) -> None:
        while True:
            match await commands.get():
                case RunAttempt(claimed=claimed, termination=termination):
                    self._post(
                        AttemptEnded(
                            task.identifier,
                            serial,
                            claimed.attempt,
                            await run_attempt(
                                self._tasks[task.name],
                                claimed.task.argument,
                                name=task.name,
                                termination=termination,
                            ),
                        )
                    )

                case ExitHost():
                    return

    # --- the functions of ManagedTasks and its handles --------------------------------

    def _require_loop(self) -> None:
        if get_running_loop() is not self._loop:
            raise RuntimeError(
                "ManagedTasks is used on another event loop than the one its block was entered on"
            )

    async def _ask[T](
        self,
        event: Callable[[_FutureReply[T]], Event],
        abandoned: Callable[[_FutureReply[T]], Event] | None = None,
        /,
    ) -> T:
        # the coordinator answers, so that nothing of the runtime is touched by the caller
        reply: _FutureReply[T] = _FutureReply(self._loop.create_future())
        self._replies.add(cast(_FutureReply[object], reply))
        try:
            self._post(event(reply))
            return await reply.future

        finally:
            self._replies.discard(cast(_FutureReply[object], reply))
            if abandoned is not None:
                self._post(abandoned(reply))

    async def _call[T](
        self,
        call: Callable[[], Coroutine[None, None, T]],
        /,
    ) -> T:
        return await self._ask(lambda reply: StoreCallAsked(call, reply, copy_context()))

    def _closed(self) -> bool:
        return isinstance(self._coordinating().lifecycle, Closed)

    async def _available(self) -> bool:
        return isinstance(self._coordinating().lifecycle, Open)

    def _handle(
        self,
        identifier: UUID,
        name: str,
        /,
    ) -> ManagedTask[State | None]:
        async def status() -> TaskStatus:
            return await self._status(identifier)

        async def outcome() -> TaskOutcome[State | None]:
            return await self._outcome(identifier, name)

        async def cancel() -> TaskCancellation:
            return await self._cancel(identifier, name)

        return new_handle(
            identifier=identifier,
            name=name,
            status=status,
            outcome=outcome,
            cancel=cancel,
        )

    async def _spawn[Result: State | None](
        self,
        call: RegisteredTaskCall[Result],
        /,
        *,
        time_limit: float | None,
        origin: ObservabilityLink | None,
    ) -> ManagedTask[Result]:
        self._require_loop()
        if not isinstance(self._coordinating().lifecycle, Open):
            raise RuntimeError("ManagedTasks runtime is closing or closed, nothing is spawned")

        registered: RegisteredTask[Never, State | None] | None = self._tasks.get(call.name)
        if registered is None:
            raise ValueError(f"Managed task '{call.name}' is not listed in this runtime")

        if registered.registration != call.registration:
            raise ValueError(
                f"Managed task '{call.name}' is not the task listed in this runtime under its name"
            )

        task: NewTask = NewTask(
            identifier=random_identifier(),
            name=call.name,
            argument=call.argument,
            retry=registered.retry if registered.retry is not None else self._retry,
            time_limit=time_limit,
            origin=origin,
        )
        await self._ask(lambda reply: SpawnAsked(task, reply, copy_context()))
        # the registration of the call is the one of the task listed, so its result class
        # is the one of the handle
        return cast(ManagedTask[Result], self._handle(task.identifier, task.name))

    async def _lookup(
        self,
        identifier: UUID,
        /,
    ) -> ManagedTask[State | None] | None:
        self._require_loop()
        if self._closed():
            raise RuntimeError("ManagedTasks runtime is closed")

        stored: Mapping[UUID, StoredTask] = await self._call(
            lambda: self._store_session().read(frozenset((identifier,)))
        )
        item: StoredTask | None = stored.get(identifier)
        if item is None:
            return None

        return self._handle(identifier, item.task.name)

    async def _list(
        self,
        pagination: Pagination,
        /,
        *,
        matching: TaskFilter,
    ) -> Paginated[TaskSummary]:
        self._require_loop()
        if self._closed():
            raise RuntimeError("ManagedTasks runtime is closed")

        limit: int = pagination.limit
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ValueError(
                f"Task listing limit has to be an integer from 1 to {_MAX_PAGE_SIZE}: {limit}"
            )

        if pagination.arguments:
            raise ValueError("Task listing takes no pagination arguments, use matching")

        after: TaskKeyset | None = decode_task_page_token(pagination.token)
        summaries: Sequence[TaskSummary] = await self._call(
            lambda: self._store_session().query(matching, after=after, limit=limit)
        )
        # a token is given for a full page only, so that a partial page ends a traversal
        token: str | None = None
        if len(summaries) >= limit:
            last: TaskSummary = summaries[limit - 1]
            token = encode_task_page_token(
                TaskKeyset(created=last.created, identifier=last.identifier)
            )

        return Paginated(summaries[:limit], pagination=pagination.with_token(token))

    async def _status(
        self,
        identifier: UUID,
        /,
    ) -> TaskStatus:
        self._require_loop()
        if self._closed():
            if self._coordinating().released(identifier) is not None:
                return "finished"

            raise RuntimeError("ManagedTasks runtime is closed")

        stored: Mapping[UUID, StoredTask] = await self._call(
            lambda: self._store_session().read(frozenset((identifier,)))
        )
        item: StoredTask | None = stored.get(identifier)
        if item is None:
            raise LookupError(f"Managed task {identifier} is not held by the store")

        return item.status.status

    async def _outcome(
        self,
        identifier: UUID,
        name: str,
        /,
    ) -> TaskOutcome[State | None]:
        self._require_loop()
        if self._closed():
            released: TaskOutcome[State | None] | None = self._coordinating().released(identifier)
            if released is None:
                raise RuntimeError("ManagedTasks runtime is closed")

            return released

        if name not in self._tasks:
            raise ValueError(
                f"Managed task '{name}' is not listed in this runtime, its result is unknown"
            )

        # a task decided by this runtime is resolved when it records, any other is
        # read from the store, at the next polling interval at the latest
        return await self._ask(
            lambda reply: OutcomeAsked(identifier, reply),
            lambda reply: OutcomeAbandoned(identifier, reply),
        )

    async def _cancel(
        self,
        identifier: UUID,
        name: str,
        /,
    ) -> TaskCancellation:
        self._require_loop()
        if self._closed():
            if self._coordinating().released(identifier) is not None:
                return "finished"

            raise RuntimeError("ManagedTasks runtime is closed")

        result: TaskCancellation = await self._ask(
            lambda reply: CancelAsked(identifier, reply, copy_context())
        )
        if result == "cancelled":
            # no runtime holds a task scope for a task which was not running
            record_debug("Managed task %s cancelled before it ran: %s", name, identifier)

        return result
