from features.todos.types import TodoCompletion
from features.todos.user_tasks import complete_todo_task
from haiway import State

__all__ = [
    "Todos",
]


class Todos(State):
    complete: TodoCompletion = complete_todo_task
