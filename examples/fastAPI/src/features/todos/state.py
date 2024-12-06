from haiway import State

from features.todos.types import TodoCompletion
from features.todos.user_tasks import complete_todo_task

__all__ = [
    "Todos",
]


class Todos(State):
    complete: TodoCompletion = complete_todo_task
