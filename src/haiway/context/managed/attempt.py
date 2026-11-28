from asyncio import CancelledError, Task, get_running_loop, wait
from collections.abc import Iterator
from typing import Literal, Never, cast, final

from haiway.attributes import State
from haiway.context.disposables import Disposables
from haiway.context.managed.encoding import encode_task_result
from haiway.context.managed.outcomes import exception_type_name
from haiway.context.managed.registration import RegisteredTask
from haiway.context.managed.types import TaskFailureRecord
from haiway.context.scope import ContextScope

__all__ = (
    "AttemptCancelledLocally",
    "AttemptRaised",
    "AttemptResult",
    "AttemptReturned",
    "AttemptTerminated",
    "AttemptTermination",
    "Termination",
    "is_interpreter_exit",
    "run_attempt",
)

type Termination = Literal["cancel", "time_limit", "close"]


@final
class AttemptTermination:
    # the termination requested for one attempt, decided by the first request. Created for an
    # attempt before its task exists: a request made until the task is bound to it is kept and
    # delivered at the binding

    __slots__ = (
        "_cancelling",
        "_reason",
        "_task",
    )

    def __init__(self) -> None:
        self._reason: Termination | None = None
        self._task: Task[str] | None = None
        # a cancellation which came before the task was bound to this termination
        self._cancelling: bool = False

    @property
    def reason(self) -> Termination | None:
        return self._reason

    def bind(
        self,
        task: Task[str],
        /,
    ) -> None:
        assert self._task is None  # nosec: B101
        self._task = task
        if self._cancelling:
            task.cancel()

    def request(
        self,
        reason: Termination,
        /,
    ) -> bool:
        # whether this request decided the end, the first one does. The cancellation is
        # delivered each time, a function which swallowed one can be cancelled again.
        task: Task[str] | None = self._task
        # a request arriving once the attempt ended is not one, whatever the loop did
        # between the end of the attempt and the moment it is observed
        if task is not None and task.done():
            return False

        decided: bool = self._reason is None
        if decided:
            self._reason = reason

        self._cancel(task)
        return decided

    def deliver(self) -> None:
        # a cancellation which decides nothing, for an attempt whose end is discarded
        task: Task[str] | None = self._task
        if task is not None and task.done():
            return

        self._cancel(task)

    def _cancel(
        self,
        task: Task[str] | None,
        /,
    ) -> None:
        if task is None:
            self._cancelling = True

        else:
            task.cancel()


@final
class AttemptReturned:
    __slots__ = ("text",)

    def __init__(
        self,
        text: str,
        /,
    ) -> None:
        self.text: str = text


@final
class AttemptRaised:
    __slots__ = ("failure",)

    def __init__(
        self,
        failure: TaskFailureRecord,
        /,
    ) -> None:
        self.failure: TaskFailureRecord = failure


@final
class AttemptCancelledLocally:
    # the attempt was cancelled while no termination was requested
    __slots__ = ()


@final
class AttemptTerminated:
    __slots__ = (
        "discarded",
        "reason",
    )

    def __init__(
        self,
        reason: Termination,
        discarded: Literal["returned", "raised"] | None,
        /,
    ) -> None:
        self.reason: Termination = reason
        # what the attempt did after the request, which is not recorded
        self.discarded: Literal["returned", "raised"] | None = discarded


type AttemptResult = AttemptReturned | AttemptRaised | AttemptCancelledLocally | AttemptTerminated


@final
class _Preparation:
    __slots__ = (
        "completed",
        "returned",
    )

    def __init__(self) -> None:
        self.completed: bool = False
        # the function returned, the exit of its scope may still be under way
        self.returned: bool = False


async def _attempt(
    registered: RegisteredTask[Never, State | None],
    argument: str,
    name: str,
    preparation: _Preparation,
) -> str:
    # no state, no preset: the registry entry of the name is the preset of the attempt
    async with ContextScope(
        name=name,
        presets=None,
        state=(),
        disposables=Disposables.of(),
        observability=None,
        kind="internal",
        isolated=False,
    ):
        function = registered.bind(argument)
        preparation.completed = True
        value: State | None = await function()
        preparation.returned = True
        if not isinstance(value, registered.result):
            raise TypeError(f"Managed task '{name}' returned a value of an unexpected class")

        return encode_task_result(value)


def _leaves(
    exception: BaseException,
    /,
) -> Iterator[BaseException]:
    if isinstance(exception, BaseExceptionGroup):
        for nested in cast(BaseExceptionGroup[BaseException], exception).exceptions:
            yield from _leaves(nested)

    else:
        yield exception


def _is_cancellation(
    exception: BaseException,
    /,
) -> bool:
    return all(isinstance(leaf, CancelledError) for leaf in _leaves(exception))


def is_interpreter_exit(
    exception: BaseException,
    /,
) -> bool:
    # an error which is neither an `Exception` nor a cancellation, among the leaves
    return any(not isinstance(leaf, Exception | CancelledError) for leaf in _leaves(exception))


async def run_attempt(
    registered: RegisteredTask[Never, State | None],
    argument: str,
    *,
    name: str,
    termination: AttemptTermination,
) -> AttemptResult:
    # runs one attempt as a task of its own, in an attempt scope: a child of the calling task,
    # so that terminating it never reaches the caller, which goes on serving its task scope.
    # Its termination is requested through ``termination``, which the runtime holds. Raises
    # what the attempt raised when it is no ``Exception`` and no cancellation, such as
    # ``KeyboardInterrupt`` or ``SystemExit``
    preparation: _Preparation = _Preparation()
    task: Task[str] = get_running_loop().create_task(
        _attempt(registered, argument, name, preparation),
    )
    termination.bind(task)
    try:
        await wait({task})

    except CancelledError:
        # the caller itself is being cancelled, nothing else ends an attempt through it
        task.cancel()
        await wait({task})
        interrupted: BaseException | None = None if task.cancelled() else task.exception()
        if interrupted is None or not is_interpreter_exit(interrupted):
            raise

        # an interpreter exit which stopped the loop before this was resumed, found by the
        # cancellation of every task, is the end of the attempt, so nothing is released

    reason: Termination | None = termination.reason
    exception: BaseException | None = CancelledError() if task.cancelled() else task.exception()
    if exception is not None and is_interpreter_exit(exception):
        raise exception  # no recording of an interpreter exit

    if reason is not None:
        if exception is None:
            return AttemptTerminated(reason, "returned")

        if _is_cancellation(exception):
            # a value which the function returned before the release of its scope was cut
            return AttemptTerminated(reason, "returned" if preparation.returned else None)

        return AttemptTerminated(reason, "raised")

    if exception is None:
        return AttemptReturned(task.result())

    if _is_cancellation(exception):
        return AttemptCancelledLocally()

    return AttemptRaised(
        TaskFailureRecord(
            kind="raised" if preparation.completed else "preparation",
            exception_type=exception_type_name(exception),
        )
    )
