from collections.abc import Iterable
from typing import Never

from haiway import State
from haiway.context.managed import (
    ManagedTask,
    ManagedTasks,
    RegisteredTask,
    RegisteredTaskCall,
    TaskCompleted,
    TaskOutcome,
    managed_task,
)


class Argument(State):
    n: int


class Result(State):
    x: int


@managed_task()
async def returning(argument: Argument) -> Result:
    return Result(x=argument.n)


@managed_task()
async def returning_none(argument: Argument) -> None:
    return None


def accept_tasks(tasks: Iterable[RegisteredTask[Never, State | None]]) -> int:
    return sum(1 for _ in tasks)


def accept_outcome(outcome: TaskOutcome[State | None]) -> TaskOutcome[State | None]:
    return outcome


def accept_completed(completed: TaskCompleted[State | None]) -> State | None:
    return completed.value


def accept_call(call: RegisteredTaskCall[State | None]) -> RegisteredTaskCall[State | None]:
    return call


def narrow_outcome(outcome: TaskOutcome[State | None]) -> TaskOutcome[Result]:
    # negative control: pyright has to report this narrowing, and the ignore comment
    # itself is an error (reportUnnecessaryTypeIgnoreComment) once it no longer does
    return outcome  # pyright: ignore[reportReturnType]


def narrow_task(
    task: RegisteredTask[Never, State | None],
) -> RegisteredTask[Argument, Result]:
    # negative control: a task of any argument is not a task of this argument
    return task  # pyright: ignore[reportReturnType]


def test_registered_tasks_assignable_to_listed_tasks() -> None:
    assert accept_tasks((returning, returning_none)) == 2


def test_task_call_widens() -> None:
    call: RegisteredTaskCall[Result] = returning(Argument(n=1))
    assert accept_call(call).name == "returning"
    assert accept_call(returning_none(Argument(n=1))).name == "returning_none"


def test_outcome_widens() -> None:
    completed: TaskCompleted[Result] = TaskCompleted(Result(x=1))
    outcome: TaskOutcome[Result] = completed
    assert accept_outcome(outcome) == completed
    assert accept_completed(completed) == Result(x=1)
    assert accept_completed(TaskCompleted(None)) is None


def accept_handle(handle: ManagedTask[State | None]) -> ManagedTask[State | None]:
    return handle


def narrow_handle(handle: ManagedTask[State | None]) -> ManagedTask[Result]:
    # negative control: a handle of any result is not a handle of this result
    return handle  # pyright: ignore[reportReturnType]


async def spawn_gives_the_result_of_the_task() -> ManagedTask[Result]:
    return await ManagedTasks.spawn(returning(Argument(n=1)))


async def spawned_handles_widen() -> ManagedTask[State | None]:
    return accept_handle(await ManagedTasks.spawn(returning(Argument(n=1))))


async def spawned_handle_of_none_widens() -> ManagedTask[State | None]:
    return accept_handle(await ManagedTasks.spawn(returning_none(Argument(n=1))))
