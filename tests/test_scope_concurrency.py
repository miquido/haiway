import asyncio
import logging
from types import TracebackType
from uuid import UUID

from pytest import LogCaptureFixture, mark, raises

from haiway import State, ctx
from haiway.context.closing import ContextClosing


class ExampleState(State):
    value: str = "example"


class FakeException(Exception):
    pass


# --- spawned task failures do not cancel the scope body ---------------------------------


@mark.asyncio
async def test_failed_task_does_not_cancel_scope_body():
    body_completed = False

    async def failing() -> None:
        raise FakeException("task failure")

    # the body runs to its end - the failure is surfaced when the scope exits
    with raises(FakeException, match="task failure"):
        async with ctx.scope("failing-task"):
            ctx.spawn(failing)
            await asyncio.sleep(0.01)
            body_completed = True

    assert body_completed


@mark.asyncio
async def test_failed_task_can_be_handled_by_the_body_awaiting_it():
    handled = False

    async def failing() -> None:
        raise FakeException("task failure")

    with raises(FakeException):
        async with ctx.scope("handled-task"):
            task = ctx.spawn(failing)
            try:
                await task

            except FakeException:
                handled = True

    assert handled


@mark.asyncio
async def test_failed_task_does_not_cancel_sibling_tasks():
    sibling_completed = asyncio.Event()

    async def failing() -> None:
        raise FakeException("task failure")

    async def sibling() -> None:
        await asyncio.sleep(0.01)
        sibling_completed.set()

    with raises(FakeException):
        async with ctx.scope("sibling-tasks"):
            ctx.spawn(failing)
            ctx.spawn(sibling)

    assert sibling_completed.is_set()


@mark.asyncio
async def test_failed_task_errors_are_all_surfaced_on_exit():
    async def failing(tag: str) -> None:
        await asyncio.sleep(0)
        raise FakeException(tag)

    with raises(ExceptionGroup) as failure:
        async with ctx.scope("failing-tasks"):
            ctx.spawn(failing, "first")
            ctx.spawn(failing, "second")
            await asyncio.sleep(0.01)  # not cancelled by the failures

    assert sorted(str(error) for error in failure.value.exceptions) == ["first", "second"]


@mark.asyncio
async def test_task_failure_does_not_cancel_enclosing_scope_body():
    parent_completed = False

    async def failing() -> None:
        raise FakeException("task failure")

    with raises(FakeException):
        async with ctx.scope("parent"):
            async with ctx.scope("child"):
                ctx.spawn(failing)
                await asyncio.sleep(0.01)

            parent_completed = True  # unreachable only when the child scope fails

    # the failure leaves the child scope as an ordinary exception of its body
    assert not parent_completed


@mark.asyncio
async def test_body_error_takes_precedence_over_task_errors():
    async def failing() -> None:
        raise ValueError("task failure")

    with raises(FakeException, match="body failure") as failure:
        async with ctx.scope("body-and-task"):
            ctx.spawn(failing)
            await asyncio.sleep(0)
            raise FakeException("body failure")

    assert isinstance(failure.value.__cause__, ValueError)


@mark.asyncio
async def test_cancelled_scope_cancels_running_tasks():
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def scoped() -> None:
        async with ctx.scope("cancelled"):
            ctx.spawn(worker)
            await asyncio.sleep(10)

    host = asyncio.ensure_future(scoped())
    await started.wait()
    host.cancel()

    with raises(asyncio.CancelledError):
        await host

    assert cancelled.is_set()


# --- tasks can't be spawned while disposables are being prepared ------------------------


class SpawningDisposable:
    def __init__(self) -> None:
        self.error: BaseException | None = None

    async def __aenter__(self) -> ExampleState:
        async def noop() -> None:
            pass

        try:
            ctx.spawn(noop)

        except BaseException as exc:
            self.error = exc

        return ExampleState()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass


@mark.asyncio
async def test_spawn_within_disposable_preparation_is_rejected_in_root_scope():
    disposable = SpawningDisposable()
    async with ctx.scope("root", disposables=(disposable,)):
        pass

    assert isinstance(disposable.error, RuntimeError)
    assert "disposable" in str(disposable.error).lower()


@mark.asyncio
async def test_spawn_within_disposable_preparation_is_rejected_in_nested_scope():
    disposable = SpawningDisposable()
    async with ctx.scope("outer"):
        async with ctx.scope("inner", disposables=(disposable,)):
            pass

    # must not attach the task to the enclosing scope
    assert isinstance(disposable.error, RuntimeError)


@mark.asyncio
async def test_spawn_within_scope_body_works_after_disposables_prepared():
    disposable = SpawningDisposable()
    completed = asyncio.Event()

    async def worker() -> None:
        completed.set()

    async with ctx.scope("root", disposables=(disposable,)):
        ctx.spawn(worker)

    assert completed.is_set()


@mark.asyncio
async def test_spawn_background_within_disposable_preparation_is_allowed():
    errors: list[BaseException] = []

    class BackgroundDisposable:
        async def __aenter__(self) -> ExampleState:
            async def noop() -> None:
                pass

            try:
                await ctx.spawn_background(noop)

            except BaseException as exc:
                errors.append(exc)

            return ExampleState()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            pass

    async with ctx.scope("root", disposables=(BackgroundDisposable(),)):
        pass

    assert errors == []


# --- disposal is ensured -----------------------------------------------------------------


class SlowDisposable:
    def __init__(self, name: str, log: list[str], delay: float = 0.05) -> None:
        self.name = name
        self.log = log
        self.delay = delay

    async def __aenter__(self) -> ExampleState:
        return ExampleState(value=self.name)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.log.append(f"{self.name}:start")
        await asyncio.sleep(self.delay)
        self.log.append(f"{self.name}:end")


@mark.asyncio
async def test_disposal_completes_when_cancelled_during_exit():
    log: list[str] = []
    entered = asyncio.Event()

    async def scoped() -> None:
        async with ctx.scope(
            "disposal",
            disposables=(SlowDisposable("a", log), SlowDisposable("b", log)),
        ):
            entered.set()

    host = asyncio.ensure_future(scoped())
    await entered.wait()
    await asyncio.sleep(0.01)  # within the disposal
    host.cancel()  # cancelled while the disposables are still exiting

    with raises(asyncio.CancelledError):
        await host

    assert sorted(log) == ["a:end", "a:start", "b:end", "b:start"]


@mark.asyncio
async def test_disposal_completes_when_cancelled_repeatedly_during_exit():
    log: list[str] = []
    entered = asyncio.Event()

    async def scoped() -> None:
        async with ctx.scope(
            "disposal-repeated",
            disposables=(SlowDisposable("a", log),),
        ):
            entered.set()

    host = asyncio.ensure_future(scoped())
    await entered.wait()
    await asyncio.sleep(0.01)
    host.cancel()
    await asyncio.sleep(0.005)
    host.cancel()

    with raises(asyncio.CancelledError):
        await host

    assert log == ["a:start", "a:end"]


@mark.asyncio
async def test_disposal_completes_when_the_scope_body_is_cancelled():
    log: list[str] = []
    started = asyncio.Event()

    async def scoped() -> None:
        async with ctx.scope(
            "disposal-body-cancelled",
            disposables=(SlowDisposable("a", log),),
        ):
            started.set()
            await asyncio.sleep(10)

    host = asyncio.ensure_future(scoped())
    await started.wait()
    host.cancel()
    await asyncio.sleep(0.01)
    host.cancel()  # second cancellation lands while the disposal runs

    with raises(asyncio.CancelledError):
        await host

    assert log == ["a:start", "a:end"]


# --- spawn outside of a scope ------------------------------------------------------------


@mark.asyncio
async def test_spawn_outside_of_scope_logs_debug_message(caplog: LogCaptureFixture):
    done = asyncio.Event()

    async def worker() -> None:
        done.set()

    with caplog.at_level(logging.DEBUG):
        ctx.spawn(worker)
        await done.wait()

    messages: list[str] = [
        record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG
    ]
    assert any("outside" in message and "background" in message for message in messages)


@mark.asyncio
async def test_spawn_within_scope_does_not_log_fallback(caplog: LogCaptureFixture):
    async def worker() -> None:
        pass

    with caplog.at_level(logging.DEBUG):
        async with ctx.scope("scoped"):
            ctx.spawn(worker)

    assert not any("background" in record.getMessage() for record in caplog.records)


# --- closing future ----------------------------------------------------------------------


@mark.asyncio
async def test_closing_completes_with_scope_id_when_scope_body_fails():
    closing: asyncio.Future[UUID] | None = None

    with raises(FakeException):
        async with ctx.scope("closing-failure"):
            closing = ContextClosing.current()
            raise FakeException("body failure")

    # the error of the body is not a result of waiting for the scope to end
    assert closing is not None
    assert closing.done()
    assert isinstance(closing.result(), UUID)


@mark.asyncio
async def test_closing_completes_with_scope_id_when_scope_is_cancelled():
    closing: asyncio.Future[UUID] | None = None
    started = asyncio.Event()

    async def scoped() -> None:
        nonlocal closing
        async with ctx.scope("closing-cancelled"):
            closing = ContextClosing.current()
            started.set()
            await asyncio.sleep(10)

    host = asyncio.ensure_future(scoped())
    await started.wait()
    host.cancel()

    with raises(asyncio.CancelledError):
        await host

    assert closing is not None
    assert closing.done()
    assert isinstance(closing.result(), UUID)


@mark.asyncio
async def test_closing_waiter_is_released_when_scope_body_fails():
    released = asyncio.Event()

    async def waiting() -> None:
        closing = ContextClosing.current()
        try:
            await closing

        finally:
            released.set()

    with raises(FakeException):
        async with ctx.scope("closing-waiter"):
            ctx.spawn(waiting)
            await asyncio.sleep(0)
            raise FakeException("body failure")

    assert released.is_set()


# --- isolated scopes are shielded from cancellation --------------------------------------


@mark.asyncio
async def test_isolated_scope_tasks_survive_cancellation_of_the_host():
    started = asyncio.Event()
    completed = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.sleep(0.05)
            completed.set()

        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def scoped() -> None:
        async with ctx.scope("isolated", isolated=True):
            ctx.spawn(worker)
            await asyncio.sleep(10)

    host = asyncio.ensure_future(scoped())
    await started.wait()
    host.cancel()

    # the cancellation is still delivered to the host once the scope is done
    with raises(asyncio.CancelledError):
        await host

    assert completed.is_set()
    assert not cancelled.is_set()


@mark.asyncio
async def test_non_isolated_scope_tasks_are_cancelled_with_the_host():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def scoped() -> None:
        async with ctx.scope("shared"):
            ctx.spawn(worker)
            await asyncio.sleep(10)

    host = asyncio.ensure_future(scoped())
    await started.wait()
    host.cancel()

    with raises(asyncio.CancelledError):
        await host

    assert cancelled.is_set()


@mark.asyncio
async def test_isolated_scope_task_errors_stay_within_the_isolated_scope():
    parent_completed = False

    async def failing() -> None:
        raise FakeException("task failure")

    async with ctx.scope("parent"):
        with raises(FakeException):
            async with ctx.scope("isolated", isolated=True):
                ctx.spawn(failing)

        parent_completed = True

    assert parent_completed


@mark.asyncio
async def test_disposal_errors_are_logged_when_cancellation_takes_precedence(
    caplog: LogCaptureFixture,
):
    entered = asyncio.Event()

    class FailingDisposable:
        async def __aenter__(self) -> ExampleState:
            return ExampleState()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            await asyncio.sleep(0.02)
            raise FakeException("disposal failure")

    async def scoped() -> None:
        async with ctx.scope("failing-disposal", disposables=(FailingDisposable(),)):
            entered.set()

    host = asyncio.ensure_future(scoped())
    await entered.wait()
    await asyncio.sleep(0.005)
    host.cancel()

    with raises(asyncio.CancelledError):
        await host

    assert any(
        record.exc_info is not None and isinstance(record.exc_info[1], FakeException)
        for record in caplog.records
    )
