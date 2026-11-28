import inspect
from collections.abc import Callable, Coroutine, Iterable, Mapping
from typing import Any, Never, cast, final
from uuid import UUID, uuid4

from haiway.attributes import State
from haiway.context.managed.encoding import encode_task_argument
from haiway.context.managed.types import TaskRetryPolicy
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = (
    "RegisteredTask",
    "RegisteredTaskCall",
    "managed_task",
    "registered_tasks_by_name",
)


@final
class _Construction:
    __slots__ = ()


_CONSTRUCTION: _Construction = _Construction()


@final
class RegisteredTaskCall[Result: State | None]:
    """
    A registered task bound to its encoded argument, ready to be spawned.

    Created only by ``RegisteredTask.__call__``, so the argument of every call has passed
    its checks.
    """

    __slots__ = (
        "_argument",
        "_name",
        "_registration",
        "_result",
    )

    def __init__(
        self,
        construction: _Construction,
        /,
        *,
        name: str,
        argument: str,
        result: type[Result],
        registration: UUID,
    ) -> None:
        if construction is not _CONSTRUCTION:
            raise TypeError("RegisteredTaskCall is created by calling a registered task")

        self._name: str
        object.__setattr__(self, "_name", name)
        self._argument: str
        object.__setattr__(self, "_argument", argument)
        self._result: type[Result]
        object.__setattr__(self, "_result", result)
        self._registration: UUID
        object.__setattr__(self, "_registration", registration)

    @property
    def name(self) -> str:
        """The registered name."""
        return self._name

    @property
    def argument(self) -> str:
        """The JSON of the argument, checked by ``RegisteredTask.__call__``."""
        return self._argument

    @property
    def result(self) -> type[Result]:
        """The result class of the registered task, ``NoneType`` for ``None``."""
        return self._result

    @property
    def registration(self) -> UUID:
        """The registration of the ``RegisteredTask`` this call was made of."""
        return self._registration

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


@final
class RegisteredTask[Argument: State, Result: State | None]:
    """
    A coroutine function registered with ``managed_task``.

    Applying ``managed_task`` is the only way to create one.
    """

    __slots__ = (
        "_bind",
        "_encode",
        "_name",
        "_registration",
        "_result",
        "_retry",
    )

    def __init__(
        self,
        construction: _Construction,
        /,
        *,
        name: str,
        retry: TaskRetryPolicy | None,
        result: type[Result],
        registration: UUID,
        encode: Callable[[State], str],
        bind: Callable[[str], Callable[[], Coroutine[None, None, Result]]],
    ) -> None:
        if construction is not _CONSTRUCTION:
            raise TypeError("RegisteredTask is created by applying managed_task")

        # none of these mentions `Argument`, which keeps it contravariant
        self._name: str
        object.__setattr__(self, "_name", name)
        self._retry: TaskRetryPolicy | None
        object.__setattr__(self, "_retry", retry)
        self._result: type[Result]
        object.__setattr__(self, "_result", result)
        self._registration: UUID
        object.__setattr__(self, "_registration", registration)
        self._encode: Callable[[State], str]
        object.__setattr__(self, "_encode", encode)
        self._bind: Callable[[str], Callable[[], Coroutine[None, None, Result]]]
        object.__setattr__(self, "_bind", bind)

    @property
    def name(self) -> str:
        """The registered name."""
        return self._name

    @property
    def retry(self) -> TaskRetryPolicy | None:
        """The retry policy override, ``None`` for the default of the runtime."""
        return self._retry

    @property
    def result(self) -> type[Result]:
        """The annotated return class, ``NoneType`` for ``None``."""
        return self._result

    @property
    def registration(self) -> UUID:
        """The identifier drawn when ``managed_task`` was applied."""
        return self._registration

    def __call__(
        self,
        argument: Argument,
        /,
    ) -> RegisteredTaskCall[Result]:
        """
        Bind the task to an argument.

        Parameters
        ----------
        argument : Argument
            The argument of the task, encoded with ``to_json`` and decoded back to check it.

        Returns
        -------
        RegisteredTaskCall[Result]
            The call holding the registered name, the encoded argument, the result class
            and the registration.

        Raises
        ------
        TypeError
            When ``argument`` is not an instance of the parameter's class.
        ValueError
            When ``argument`` fails to encode or to decode back, or holds U+0000 in a string.
        """
        return RegisteredTaskCall(
            _CONSTRUCTION,
            name=self._name,
            argument=self._encode(argument),
            result=self._result,
            registration=self._registration,
        )

    def bind(
        self,
        argument: str,
        /,
    ) -> Callable[[], Coroutine[None, None, Result]]:
        """
        Decode a stored argument and bind the function to it.

        Parameters
        ----------
        argument : str
            The stored JSON of the argument. Keys the parameter's class does not define
            are skipped.

        Returns
        -------
        Callable[[], Coroutine[None, None, Result]]
            The function with its argument, to be called for each attempt.

        Raises
        ------
        ValueError
            When decoding fails.
        """
        return self._bind(argument)

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


def registered_tasks_by_name(
    tasks: Iterable[RegisteredTask[Never, State | None]],
    /,
) -> Mapping[str, RegisteredTask[Never, State | None]]:
    by_name: dict[str, RegisteredTask[Never, State | None]] = {}
    for task in tasks:
        if task.name in by_name:
            raise ValueError(f"TaskRuntime tasks hold more than one task named '{task.name}'")

        by_name[task.name] = task

    return by_name


def managed_task[Argument: State, Result: State | None](
    *,
    name: str | None = None,
    retry: TaskRetryPolicy | None = None,
) -> Callable[
    [Callable[[Argument], Coroutine[None, None, Result]]],
    RegisteredTask[Argument, Result],
]:
    """
    Register a coroutine function as a managed task.

    The function takes exactly one positional parameter without a default, annotated with
    a ``State`` class, and returns a ``State`` class or ``None``. Its argument and result
    are stored as JSON.

    The registered name is ``name``, or else the ``__name__`` of the function, read once
    when the decorator is applied - never its qualified name or a module path. Renaming
    the function therefore changes its registered name, so stored tasks of the old name
    have an unlisted name in every runtime of the new code: they wait in a runtime with
    ``unsupported="leave"`` and are failed by one with ``unsupported="fail"``. An explicit
    ``name`` keeps the identity stable across renames and moves. Two functions of one
    ``__name__`` in one runtime are rejected, and across runtimes sharing one store they
    share one registered name.

    Parameters
    ----------
    name : str | None, default=None
        The registered name. When absent, the ``__name__`` of the function.
    retry : TaskRetryPolicy | None, default=None
        The retry policy of the task. When absent, the default of the runtime.

    Returns
    -------
    Callable[[Callable[[Argument], Coroutine[None, None, Result]]], RegisteredTask]
        The decorator returning the ``RegisteredTask``, which gets a new registration
        each time it is applied.

    Raises
    ------
    ValueError
        When ``name`` is given and empty, or when it is absent and the ``__name__`` of the
        function is empty.
    TypeError
        When applied to a function which does not take exactly one positional ``State``
        parameter without a default, does not return a ``State`` class or ``None``, or when
        ``name`` is absent and the function has no ``__name__`` of type ``str``.
    """
    if name is not None and not name:
        raise ValueError("managed_task name must not be empty")

    def register(
        function: Callable[[Argument], Coroutine[None, None, Result]],
        /,
    ) -> RegisteredTask[Argument, Result]:
        registered_name: str
        if name is not None:
            registered_name = name

        else:
            function_name: object = getattr(function, "__name__", None)
            if not isinstance(function_name, str):
                raise TypeError(
                    "managed_task needs a name, the function has no __name__ of type str"
                )

            if not function_name:
                raise ValueError("managed_task needs a name, the function __name__ is empty")

            registered_name = function_name

        # the classes were read from the signature, which the annotations of `function` mirror
        argument_class: type[Argument] = cast(type[Argument], _argument_class(function))
        result_class: type[Result] = cast(type[Result], _result_class(function))

        def encode(argument: State, /) -> str:
            if not isinstance(argument, argument_class):
                raise TypeError(
                    f"Argument of '{registered_name}' has to be {argument_class.__qualname__},"
                    f" not {type(argument).__qualname__}"
                )

            return encode_task_argument(argument, decoding=argument_class)

        def bind(argument: str, /) -> Callable[[], Coroutine[None, None, Result]]:
            decoded: Argument = argument_class.from_json(argument, ignore_extra=True)

            async def run() -> Result:
                return await function(decoded)

            return run

        return RegisteredTask(
            _CONSTRUCTION,
            name=registered_name,
            retry=retry,
            result=result_class,
            registration=uuid4(),
            encode=encode,
            bind=bind,
        )

    return register


def _signature(
    function: Callable[..., Any],
    /,
) -> inspect.Signature:
    try:
        return inspect.signature(function, eval_str=True)

    except Exception as exc:
        raise TypeError("managed_task could not read the signature of the function") from exc


def _argument_class(
    function: Callable[..., Any],
    /,
) -> type[State]:
    parameters: list[inspect.Parameter] = list(_signature(function).parameters.values())
    if len(parameters) != 1:
        raise TypeError("A managed task takes exactly one parameter")

    parameter: inspect.Parameter = parameters[0]
    if parameter.kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        raise TypeError("The parameter of a managed task has to be positional")

    if parameter.default is not inspect.Parameter.empty:
        raise TypeError("The parameter of a managed task must not have a default")

    annotation: Any = parameter.annotation
    if not (isinstance(annotation, type) and issubclass(annotation, State)):
        raise TypeError("The parameter of a managed task has to be annotated with a State class")

    return annotation


def _result_class(
    function: Callable[..., Any],
    /,
) -> type[State] | type[None]:
    annotation: Any = _signature(function).return_annotation
    if annotation is None or annotation is type(None):
        return type(None)

    if isinstance(annotation, type) and issubclass(annotation, State):
        return annotation

    raise TypeError("A managed task has to return a State class or None")
