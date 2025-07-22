# haiway.types.missing

## MissingType Objects

```python
class MissingType(type)
```

Metaclass for the Missing type implementing the singleton pattern.

Ensures that only one instance of the Missing class ever exists,
allowing for identity comparison using the 'is' operator.

## Missing Objects

```python
@final
class Missing(metaclass=MissingType)
```

Type representing absence of a value. Use MISSING constant for its value.

This is a singleton class that represents the absence of a value, similar to
None but semantically different. Where None represents "no value", MISSING
represents "no value provided" or "value intentionally omitted".

The MISSING constant is the only instance of this class and should be used
for all comparisons using the 'is' operator, not equality testing.

#### is\_missing

```python
def is_missing(check: Any | Missing) -> TypeGuard[Missing]
```

Check if a value is the MISSING sentinel.

This function implements a TypeGuard that helps static type checkers
understand when a value is confirmed to be the MISSING sentinel.

Parameters
----------
check : Any | Missing
    The value to check

Returns
-------
TypeGuard[Missing]
    True if the value is MISSING, False otherwise

Examples
--------
```python
if is_missing(value):
    # Here, type checkers know that value is Missing
    provide_default()
```

#### not\_missing

```python
def not_missing(check: Value | Missing) -> TypeGuard[Value]
```

Check if a value is not the MISSING sentinel.

This function implements a TypeGuard that helps static type checkers
understand when a value is confirmed not to be the MISSING sentinel.

Parameters
----------
check : Value | Missing
    The value to check

Returns
-------
TypeGuard[Value]
    True if the value is not MISSING, False otherwise

Examples
--------
```python
if not_missing(value):
    # Here, type checkers know that value is of type Value
    process_value(value)
```

#### unwrap\_missing

```python
@overload
def unwrap_missing(check: Value | Missing, *, default: Value) -> Value
```

Substitute a default value when the input is MISSING.

This function provides a convenient way to replace the MISSING
sentinel with a default value, similar to how the or operator
works with None but specifically for the MISSING sentinel.

Parameters
----------
value : Value | Missing
    The value to check.
default : Value
    The default value to use if check is MISSING.

Returns
-------
Value
    The original value if not MISSING, otherwise the provided default

Examples
--------
```python
result = unwrap_missing(optional_value, default=default_value)
# result will be default_value if optional_value is MISSING
# otherwise it will be optional_value
```

#### unwrap\_missing

```python
@overload
def unwrap_missing(value: Value | Missing, *, default: Mapped,
                   mapping: Callable[[Value], Mapped]) -> Value | Mapped
```

Substitute a default value when the input is MISSING or map the original.

This function provides a convenient way to replace the MISSING
sentinel with a default value, similar to how the or operator
works with None but specifically for the MISSING sentinel.
Original value is mapped using provided function when not missing.

Parameters
----------
value : Value | Missing
    The value to check.
default : Mapped
    The default value to use if check is MISSING.
mapping: Callable[[Value], Result] | None = None
    Mapping to apply to the value.

Returns
-------
Mapped
    The original value with mapping applied if not MISSING, otherwise the provided default.

Examples
--------
```python
result = unwrap_missing(optional_value, default=default_value, mapping=value_map)
# result will be default_value if optional_value is MISSING
# otherwise it will be optional_value after mapping
```

# haiway.types

# haiway.types.default

## DefaultValue Objects

```python
@final
class DefaultValue()
```

Container for a default value or a factory function that produces a default value.

This class stores either a direct default value or a factory function that can
produce a default value when needed. It ensures the value or factory cannot be
modified after initialization.

The value can be retrieved by calling the instance like a function.

#### \_\_init\_\_

```python
def __init__(value: Value | Missing = MISSING,
             *,
             factory: Callable[[], Value] | Missing = MISSING) -> None
```

Initialize with either a default value or a factory function.

Parameters
----------
value : Value | Missing
    The default value to store, or MISSING if using a factory
factory : Callable[[], Value] | Missing
    A function that returns the default value when called, or MISSING if using a direct value

Raises
------
AssertionError
    If both value and factory are provided

#### \_\_call\_\_

```python
def __call__() -> Value | Missing
```

Get the default value.

Returns
-------
Value | Missing
    The stored default value, or the result of calling the factory function

#### Default

```python
def Default(value: Value | Missing = MISSING,
            *,
            factory: Callable[[], Value] | Missing = MISSING) -> Value
```

Create a default value container that appears as the actual value type.

This function creates a DefaultValue instance but returns it typed as the actual
value type it contains. This allows type checkers to treat it as if it were the
actual value while still maintaining the lazy evaluation behavior.

Parameters
----------
value : Value | Missing
    The default value to store, or MISSING if using a factory
factory : Callable[[], Value] | Missing
    A function that returns the default value when called, or MISSING if using a direct value

Returns
-------
Value
    A DefaultValue instance that appears to be of type Value for type checking purposes

Notes
-----
Only one of value or factory should be provided. If both are provided, an exception will be raised.

# haiway.context.disposables

## Disposables Objects

```python
@final
class Disposables()
```

A container for multiple Disposable resources that manages their lifecycle.

This class provides a way to handle multiple disposable resources as a single unit,
entering all of them in parallel when the container is entered and exiting all of
them when the container is exited. Any states returned by the disposables are
collected and automatically propagated to the context.

Key Features
------------
- Parallel setup and cleanup of all contained disposables
- Automatic state collection and context propagation
- Thread-safe cross-event-loop disposal
- Exception handling with BaseExceptionGroup for multiple failures
- Immutable after initialization

The class is designed to work seamlessly with ctx.scope() and ensures proper
resource cleanup even when exceptions occur during setup or teardown.

Examples
--------
Creating and using multiple disposables:

>>> from haiway import ctx
>>> async def main():
...     disposables = Disposables(
...         database_disposable(),
...         cache_disposable()
...     )
...
...     async with ctx.scope("app", disposables=disposables):
...         # Both DatabaseState and CacheState are available
...         db = ctx.state(DatabaseState)
...         cache = ctx.state(CacheState)

Direct context manager usage:

>>> async def process_data():
...     disposables = Disposables(
...         create_temp_file_disposable(),
...         create_network_connection_disposable()
...     )
...
...     async with disposables:
...         # Resources are set up in parallel
...         temp_file = ctx.state(TempFileState)
...         network = ctx.state(NetworkState)
...
...         # Process data using both resources
...
...     # All resources cleaned up automatically

#### \_\_init\_\_

```python
def __init__(*disposables: Disposable) -> None
```

Initialize a collection of disposable resources.

Parameters
----------
*disposables: Disposable
    Variable number of disposable resources to be managed together.

#### \_\_bool\_\_

```python
def __bool__() -> bool
```

Check if this container has any disposables.

Returns
-------
bool
    True if there are disposables, False otherwise.

#### prepare

```python
async def prepare() -> Iterable[State]
```

Enter all contained disposables asynchronously.

Enters all disposables in parallel and collects any State objects they return.

#### \_\_aenter\_\_

```python
async def __aenter__() -> None
```

Enter all contained disposables asynchronously.

Enters all disposables in parallel and collects any State objects they return updating
 current state context.

#### dispose

```python
async def dispose(exc_type: type[BaseException] | None = None,
                  exc_val: BaseException | None = None,
                  exc_tb: TracebackType | None = None) -> None
```

Exit all contained disposables asynchronously.

Properly disposes of all resources by calling their __aexit__ methods in parallel.
If multiple disposables raise exceptions, they are collected into a BaseExceptionGroup.

Parameters
----------
exc_type: type[BaseException] | None
    The type of exception that caused the context to be exited
exc_val: BaseException | None
    The exception that caused the context to be exited
exc_tb: TracebackType | None
    The traceback for the exception that caused the context to be exited

Raises
------
BaseExceptionGroup
    If multiple disposables raise exceptions during exit

#### \_\_aexit\_\_

```python
async def __aexit__(exc_type: type[BaseException] | None,
                    exc_val: BaseException | None,
                    exc_tb: TracebackType | None) -> None
```

Exit all contained disposables asynchronously.

Properly disposes of all resources by calling their __aexit__ methods in parallel.
If multiple disposables raise exceptions, they are collected into a BaseExceptionGroup.
Additionally, produced state context will be also exited resetting state to previous.

Parameters
----------
exc_type: type[BaseException] | None
    The type of exception that caused the context to be exited
exc_val: BaseException | None
    The exception that caused the context to be exited
exc_tb: TracebackType | None
    The traceback for the exception that caused the context to be exited

Raises
------
BaseExceptionGroup
    If multiple disposables raise exceptions during exit

# haiway.context.tasks

## TaskGroupContext Objects

```python
@final
class TaskGroupContext()
```

Context manager for managing task groups within a scope.

Provides a way to create and manage asyncio tasks within a context,
ensuring proper task lifecycle management and context propagation.
This class is immutable after initialization.

#### run

```python
@classmethod
def run(cls, function: Callable[Arguments, Coroutine[Any, Any, Result]], *args:
        Arguments.args, **kwargs: Arguments.kwargs) -> Task[Result]
```

Run a coroutine function as a task within the current task group.

If called within a TaskGroupContext, creates a task in that group.
If called outside any TaskGroupContext, creates a detached task.

Parameters
----------
function: Callable[Arguments, Coroutine[Any, Any, Result]]
    The coroutine function to run
*args: Arguments.args
    Positional arguments to pass to the function
**kwargs: Arguments.kwargs
    Keyword arguments to pass to the function

Returns
-------
Task[Result]
    The created task

#### \_\_init\_\_

```python
def __init__(task_group: TaskGroup | None = None) -> None
```

Initialize a task group context.

Parameters
----------
task_group: TaskGroup | None
    The task group to use, or None to create a new one

#### \_\_aenter\_\_

```python
async def __aenter__() -> None
```

Enter this task group context.

Enters the underlying task group and sets this context as current.

Raises
------
AssertionError
    If attempting to re-enter an already active context

#### \_\_aexit\_\_

```python
async def __aexit__(exc_type: type[BaseException] | None,
                    exc_val: BaseException | None,
                    exc_tb: TracebackType | None) -> None
```

Exit this task group context.

Restores the previous task group context and exits the underlying task group.
Silently ignores task group exceptions to avoid masking existing exceptions.

Parameters
----------
exc_type: type[BaseException] | None
    Type of exception that caused the exit
exc_val: BaseException | None
    Exception instance that caused the exit
exc_tb: TracebackType | None
    Traceback for the exception

Raises
------
AssertionError
    If the context is not active

# haiway.context.access

## ScopeContext Objects

```python
@final
class ScopeContext()
```

Context manager for executing code within a defined scope.

ScopeContext manages scope-related data and behavior including identity, state,
observability, and task coordination. It enforces immutability and provides both
synchronous and asynchronous context management interfaces.

This class should not be instantiated directly; use the ctx.scope() factory method
to create scope contexts.

## ctx Objects

```python
@final
class ctx()
```

Static access to the current scope context.

Provides static methods for accessing and manipulating the current scope context,
including creating scopes, accessing state, logging, and task management.

This class is not meant to be instantiated; all methods are static.

#### trace\_id

```python
@staticmethod
def trace_id(scope_identifier: ScopeIdentifier | None = None) -> str
```

Get the trace identifier for the specified scope or current scope.

The trace identifier is a unique identifier that can be used to correlate
logs, events, and metrics across different components and services.

Parameters
----------
scope_identifier: ScopeIdentifier | None, default=None
    The scope identifier to get the trace ID for. If None, the current scope's
    trace ID is returned.

Returns
-------
str
    The hexadecimal representation of the trace ID

Raises
------
RuntimeError
    If called outside of any scope context

#### scope

```python
@staticmethod
def scope(label: str,
          *state: State | None,
          disposables: Disposables | Iterable[Disposable] | None = None,
          task_group: TaskGroup | None = None,
          observability: Observability | Logger | None = None) -> ScopeContext
```

Prepare scope context with given parameters. When called within an existing context         it becomes nested with current context as its parent.

Parameters
----------
label: str
    name of the scope context

*state: State | None
    state propagated within the scope context, will be merged with current state by             replacing current with provided on conflict.

disposables: Disposables | Iterable[Disposable] | None
    disposables consumed within the context when entered. Produced state will automatically             be added to the scope state. Using asynchronous context is required if any disposables             were provided.

task_group: TaskGroup | None
    task group used for spawning and joining tasks within the context. Root scope will
     always have task group created even when not set.

observability: Observability | Logger | None = None
    observability solution responsible for recording and storing metrics, logs and events.             Assigning observability within existing context will result in an error.
    When not provided, logger with the scope name will be requested and used.

Returns
-------
ScopeContext
    context object intended to enter context manager with.             context manager will provide trace_id of current context.

#### updated

```python
@staticmethod
def updated(*state: State | None) -> StateContext
```

Update scope context with given state. When called within an existing context         it becomes nested with current context as its predecessor.

Parameters
----------
*state: State | None
    state propagated within the updated scope context, will be merged with current if any             by replacing current with provided on conflict

Returns
-------
StateContext
    state part of context object intended to enter context manager with it

#### disposables

```python
@staticmethod
def disposables(*disposables: Disposable | None) -> Disposables
```

Create a container for managing multiple disposable resources.

Disposables are async context managers that can provide state objects and
require proper cleanup. This method creates a Disposables container that
manages multiple disposable resources as a single unit, handling their
lifecycle and state propagation.

Parameters
----------
*disposables: Disposable | None
    Variable number of disposable resources to be managed together.
    None values are filtered out automatically.

Returns
-------
Disposables
    A container that manages the lifecycle of all provided disposables
    and propagates their state to the context when used with ctx.scope()

Examples
--------
Using disposables with database connections:

>>> from haiway import ctx
>>> async def main():
...
...     async with ctx.scope(
...         "database_work",
...         disposables=(database_connection(),)
...     ):
...         # ConnectionState is now available in context
...         conn_state = ctx.state(ConnectionState)
...         await conn_state.connection.execute("SELECT 1")

#### spawn

```python
@staticmethod
def spawn(function: Callable[Arguments, Coroutine[Any, Any, Result]], *args:
          Arguments.args, **kwargs: Arguments.kwargs) -> Task[Result]
```

Spawn an async task within current scope context task group. When called outside of context         it will spawn detached task instead.

Parameters
----------
function: Callable[Arguments, Coroutine[Any, Any, Result]]
    function to be called within the task group

*args: Arguments.args
    positional arguments passed to function call

**kwargs: Arguments.kwargs
    keyword arguments passed to function call

Returns
-------
Task[Result]
    task for tracking function execution and result

#### stream

```python
@staticmethod
def stream(source: Callable[Arguments, AsyncGenerator[Element, None]], *args:
           Arguments.args,
           **kwargs: Arguments.kwargs) -> AsyncIterator[Element]
```

Stream results produced by a generator within the proper context state.

Parameters
----------
source: Callable[Arguments, AsyncGenerator[Result, None]]
    generator streamed as the result

*args: Arguments.args
    positional arguments passed to generator call

**kwargs: Arguments.kwargs
    keyword arguments passed to generator call

Returns
-------
AsyncIterator[Result]
    iterator for accessing generated results

#### check\_cancellation

```python
@staticmethod
def check_cancellation() -> None
```

Check if current asyncio task is cancelled, raises CancelledError if so.

Allows cooperative cancellation by checking and responding to cancellation
requests at appropriate points in the code.

Raises
------
CancelledError
    If the current task has been cancelled

#### cancel

```python
@staticmethod
def cancel() -> None
```

Cancel current asyncio task.

Cancels the current running asyncio task. This will result in a CancelledError
being raised in the task.

Raises
------
RuntimeError
    If called outside of an asyncio task

#### check\_state

```python
@staticmethod
def check_state(state: type[StateType],
                *,
                instantiate_defaults: bool = False) -> bool
```

Check if state object is available in the current context.

Verifies if state object of the specified type is available the current context.
Instantiates requested state if needed and possible.

Parameters
----------
state: type[StateType]
    The type of state to check

instantiate_defaults: bool = False
    Control if default value should be instantiated during check.

Returns
-------
bool
    True if state is available, otherwise False.

#### state

```python
@staticmethod
def state(state: type[StateType],
          default: StateType | None = None) -> StateType
```

Access state from the current scope context by its type.

Retrieves state objects that have been propagated within the current execution context.
State objects are automatically made available through context scopes and disposables.
If no matching state is found, creates a default instance if possible.

Parameters
----------
state: type[StateType]
    The State class type to retrieve from the current context
default: StateType | None, default=None
    Optional default instance to return if state is not found in context.
    If None and no state is found, a new instance will be created if possible.

Returns
-------
StateType
    The state instance from the current context or a default/new instance

Raises
------
RuntimeError
    If called outside of any scope context
TypeError
    If no state is found and no default can be created

Examples
--------
Accessing configuration state:

>>> from haiway import ctx, State
>>>
>>> class ApiConfig(State):
...     base_url: str = "https://api.example.com"
...     timeout: int = 30
>>>
>>> async def fetch_data():
...     config = ctx.state(ApiConfig)
...     # Use config.base_url and config.timeout
>>>
>>> async with ctx.scope("api", ApiConfig(base_url="https://custom.api.com")):
...     await fetch_data()  # Uses custom config

Accessing state with default:

>>> cache_config = ctx.state(CacheConfig, default=CacheConfig(ttl=3600))

Within service classes:

>>> class UserService(State):
...     @classmethod
...     async def get_user(cls, user_id: str) -> User:
...         config = ctx.state(DatabaseConfig)
...         # Use config to connect to database

#### log\_error

```python
@staticmethod
def log_error(message: str,
              *args: Any,
              exception: BaseException | None = None,
              **extra: Any) -> None
```

Log using ERROR level within current scope context. When there is no current scope         root logger will be used without additional details.

Parameters
----------
message: str
    message to be written to log

*args: Any
    message format arguments

exception: BaseException | None = None
    exception associated with log, when provided full stack trace will be recorded

Returns
-------
None

#### log\_warning

```python
@staticmethod
def log_warning(message: str,
                *args: Any,
                exception: Exception | None = None,
                **extra: Any) -> None
```

Log using WARNING level within current scope context. When there is no current scope         root logger will be used without additional details.

Parameters
----------
message: str
    message to be written to log

*args: Any
    message format arguments

exception: BaseException | None = None
    exception associated with log, when provided full stack trace will be recorded

Returns
-------
None

#### log\_info

```python
@staticmethod
def log_info(message: str, *args: Any, **extra: Any) -> None
```

Log using INFO level within current scope context. When there is no current scope         root logger will be used without additional details.

Parameters
----------
message: str
    message to be written to log

*args: Any
    message format arguments

Returns
-------
None

#### log\_debug

```python
@staticmethod
def log_debug(message: str,
              *args: Any,
              exception: Exception | None = None,
              **extra: Any) -> None
```

Log using DEBUG level within current scope context. When there is no current scope         root logger will be used without additional details.

Parameters
----------
message: str
    message to be written to log

*args: Any
    message format arguments

exception: BaseException | None = None
    exception associated with log, when provided full stack trace will be recorded

Returns
-------
None

#### record

```python
@overload
@staticmethod
def record(level: ObservabilityLevel = ObservabilityLevel.DEBUG,
           *,
           attributes: Mapping[str, ObservabilityAttribute]) -> None
```

Record observability data within the current scope context.

This method has three different forms:
1. Record standalone attributes
2. Record a named event with optional attributes
3. Record a metric with a value and optional unit and attributes

Parameters
----------
level: ObservabilityLevel
    Severity level for the recording (default: DEBUG)
attributes: Mapping[str, ObservabilityAttribute]
    Key-value attributes to record
event: str
    Name of the event to record
metric: str
    Name of the metric to record
value: float | int
    Numeric value of the metric
unit: str | None
    Optional unit for the metric

# haiway.context

# haiway.context.observability

## ObservabilityLevel Objects

```python
class ObservabilityLevel(IntEnum)
```

Defines the severity levels for observability recordings.

These levels correspond to standard logging levels, allowing consistent
severity indication across different types of observability records.

## ObservabilityTraceIdentifying Objects

```python
@runtime_checkable
class ObservabilityTraceIdentifying(Protocol)
```

Protocol for accessing trace identifier in an observability system.

## ObservabilityLogRecording Objects

```python
@runtime_checkable
class ObservabilityLogRecording(Protocol)
```

Protocol for recording log messages in an observability system.

Implementations should handle formatting and storing log messages
with appropriate contextual information from the scope.

## ObservabilityEventRecording Objects

```python
@runtime_checkable
class ObservabilityEventRecording(Protocol)
```

Protocol for recording events in an observability system.

Implementations should handle recording named events with
associated attributes and appropriate contextual information.

## ObservabilityMetricRecording Objects

```python
@runtime_checkable
class ObservabilityMetricRecording(Protocol)
```

Protocol for recording metrics in an observability system.

Implementations should handle recording numeric measurements with
optional units and associated attributes.

## ObservabilityAttributesRecording Objects

```python
@runtime_checkable
class ObservabilityAttributesRecording(Protocol)
```

Protocol for recording standalone attributes in an observability system.

Implementations should handle recording contextual attributes
that are not directly associated with logs, events, or metrics.

## ObservabilityScopeEntering Objects

```python
@runtime_checkable
class ObservabilityScopeEntering(Protocol)
```

Protocol for handling scope entry in an observability system.

Implementations should record when execution enters a new scope.

## ObservabilityScopeExiting Objects

```python
@runtime_checkable
class ObservabilityScopeExiting(Protocol)
```

Protocol for handling scope exit in an observability system.

Implementations should record when execution exits a scope,
including any exceptions that caused the exit.

## Observability Objects

```python
class Observability()
```

Container for observability recording functions.

Provides a unified interface for recording various types of observability
data including logs, events, metrics, and attributes. Also handles recording
when scopes are entered and exited.

This class is immutable after initialization.

#### \_\_init\_\_

```python
def __init__(trace_identifying: ObservabilityTraceIdentifying,
             log_recording: ObservabilityLogRecording,
             metric_recording: ObservabilityMetricRecording,
             event_recording: ObservabilityEventRecording,
             attributes_recording: ObservabilityAttributesRecording,
             scope_entering: ObservabilityScopeEntering,
             scope_exiting: ObservabilityScopeExiting) -> None
```

Initialize an Observability container with recording functions.

Parameters
----------
trace_identifying: ObservabilityTraceIdentifying
    Function for identifying traces
log_recording: ObservabilityLogRecording
    Function for recording log messages
metric_recording: ObservabilityMetricRecording
    Function for recording metrics
event_recording: ObservabilityEventRecording
    Function for recording events
attributes_recording: ObservabilityAttributesRecording
    Function for recording attributes
scope_entering: ObservabilityScopeEntering
    Function called when a scope is entered
scope_exiting: ObservabilityScopeExiting
    Function called when a scope is exited

## ObservabilityContext Objects

```python
@final
class ObservabilityContext()
```

Context manager for observability within a scope.

Manages observability recording within a context, propagating the
appropriate observability handler and scope information. Records
scope entry and exit events automatically.

This class is immutable after initialization.

#### scope

```python
@classmethod
def scope(cls, scope: ScopeIdentifier, *,
          observability: Observability | Logger | None) -> Self
```

Create an observability context for a scope.

If called within an existing context, inherits the observability
handler unless a new one is specified. If called outside any context,
creates a new root context with the specified or default observability.

Parameters
----------
scope: ScopeIdentifier
    The scope identifier this context is associated with
observability: Observability | Logger | None
    The observability handler to use, or None to inherit or create default

Returns
-------
Self
    A new observability context

#### trace\_id

```python
@classmethod
def trace_id(cls, scope_identifier: ScopeIdentifier | None = None) -> str
```

Get the hexadecimal trace identifier for the specified scope or current scope.

This class method retrieves the trace identifier from the current observability context,
which can be used to correlate logs, events, and metrics across different components.

Parameters
----------
scope_identifier: ScopeIdentifier | None, default=None
    The scope identifier to get the trace ID for. If None, the current scope's
    trace ID is returned.

Returns
-------
str
    The hexadecimal representation of the trace ID

Raises
------
RuntimeError
    If called outside of any scope context

#### record\_log

```python
@classmethod
def record_log(cls, level: ObservabilityLevel, message: str, *args: Any,
               exception: BaseException | None) -> None
```

Record a log message in the current observability context.

If no context is active, falls back to the root logger.

Parameters
----------
level: ObservabilityLevel
    The severity level for this log message
message: str
    The log message text, may contain format placeholders
*args: Any
    Format arguments for the message
exception: BaseException | None
    Optional exception to associate with the log

#### record\_event

```python
@classmethod
def record_event(cls, level: ObservabilityLevel, event: str, *,
                 attributes: Mapping[str, ObservabilityAttribute]) -> None
```

Record an event in the current observability context.

Records a named event with associated attributes. Falls back to logging
an error if recording fails.

Parameters
----------
level: ObservabilityLevel
    The severity level for this event
event: str
    The name of the event
attributes: Mapping[str, ObservabilityAttribute]
    Key-value attributes associated with the event

#### record\_metric

```python
@classmethod
def record_metric(cls, level: ObservabilityLevel, metric: str, *,
                  value: float | int, unit: str | None,
                  attributes: Mapping[str, ObservabilityAttribute]) -> None
```

Record a metric in the current observability context.

Records a numeric measurement with an optional unit and associated attributes.
Falls back to logging an error if recording fails.

Parameters
----------
level: ObservabilityLevel
    The severity level for this metric
metric: str
    The name of the metric
value: float | int
    The numeric value of the metric
unit: str | None
    Optional unit for the metric (e.g., "ms", "bytes")
attributes: Mapping[str, ObservabilityAttribute]
    Key-value attributes associated with the metric

#### record\_attributes

```python
@classmethod
def record_attributes(
        cls, level: ObservabilityLevel, *,
        attributes: Mapping[str, ObservabilityAttribute]) -> None
```

Record standalone attributes in the current observability context.

Records key-value attributes not directly associated with a specific log,
event, or metric. Falls back to logging an error if recording fails.

Parameters
----------
level: ObservabilityLevel
    The severity level for these attributes
attributes: Mapping[str, ObservabilityAttribute]
    Key-value attributes to record

#### \_\_enter\_\_

```python
def __enter__() -> None
```

Enter this observability context.

Sets this context as the current one and records scope entry.

Raises
------
AssertionError
    If attempting to re-enter an already active context

#### \_\_exit\_\_

```python
def __exit__(exc_type: type[BaseException] | None,
             exc_val: BaseException | None,
             exc_tb: TracebackType | None) -> None
```

Exit this observability context.

Restores the previous context and records scope exit.

Parameters
----------
exc_type: type[BaseException] | None
    Type of exception that caused the exit
exc_val: BaseException | None
    Exception instance that caused the exit
exc_tb: TracebackType | None
    Traceback for the exception

Raises
------
AssertionError
    If the context is not active

# haiway.context.types

## MissingContext Objects

```python
class MissingContext(Exception)
```

Exception raised when attempting to access a context that doesn't exist.

This exception is raised when code attempts to access the context system
outside of an active context, such as trying to access state or scope
identifiers when no context has been established.

## MissingState Objects

```python
class MissingState(Exception)
```

Exception raised when attempting to access state that doesn't exist.

This exception is raised when code attempts to access a specific state type
that is not present in the current context and cannot be automatically
created (either because no default was provided or instantiation failed).

# haiway.context.identifier

## ScopeIdentifier Objects

```python
@final
class ScopeIdentifier()
```

Identifies and manages scope context identities.

ScopeIdentifier maintains a context-local scope identity including
scope ID, and parent ID. It provides a hierarchical structure for tracking
execution scopes, supporting both root scopes and nested child scopes.

This class is immutable after instantiation.

#### scope

```python
@classmethod
def scope(cls, label: str) -> Self
```

Create a new scope identifier.

If called within an existing scope, creates a nested scope with a new ID.
If called outside any scope, creates a root scope with new scope ID.

Parameters
----------
label: str
    The name of the scope

Returns
-------
Self
    A newly created scope identifier

#### is\_root

```python
@property
def is_root() -> bool
```

Check if this scope is a root scope.

A root scope is one that was created outside of any other scope.

Returns
-------
bool
    True if this is a root scope, False if it's a nested scope

#### \_\_enter\_\_

```python
def __enter__() -> None
```

Enter this scope identifier's context.

Sets this identifier as the current scope identifier in the context.

Raises
------
AssertionError
    If this context is already active

#### \_\_exit\_\_

```python
def __exit__(exc_type: type[BaseException] | None,
             exc_val: BaseException | None,
             exc_tb: TracebackType | None) -> None
```

Exit this scope identifier's context.

Restores the previous scope identifier in the context.

Parameters
----------
exc_type: type[BaseException] | None
    Type of exception that caused the exit
exc_val: BaseException | None
    Exception instance that caused the exit
exc_tb: TracebackType | None
    Traceback for the exception

Raises
------
AssertionError
    If this context is not active

# haiway.context.state

## ScopeState Objects

```python
@final
class ScopeState()
```

Container for state objects within a scope.

Stores state objects by their type, allowing retrieval by type.
Only one state of a given type can be stored at a time.
This class is immutable after initialization.

#### check\_state

```python
def check_state(state: type[StateType],
                *,
                instantiate_defaults: bool = False) -> bool
```

Check state object availability by its type.

If the state type is not found, attempts to instantiate a new instance of         the type if possible.

Parameters
----------
state: type[StateType]
    The type of state to check

instantiate_defaults: bool = False
    Control if default value should be instantiated during check.

Returns
-------
bool
    True if state is available, otherwise False.

#### state

```python
def state(state: type[StateType],
          default: StateType | None = None) -> StateType
```

Get a state object by its type.

If the state type is not found, attempts to use a provided default
or instantiate a new instance of the type. Raises MissingState
if neither is possible.

Parameters
----------
state: type[StateType]
    The type of state to retrieve
default: StateType | None
    Optional default value to use if state not found

Returns
-------
StateType
    The requested state object

Raises
------
MissingState
    If state not found and default not provided or instantiation fails

#### updated

```python
def updated(state: Iterable[State]) -> Self
```

Create a new ScopeState with updated state objects.

Combines the current state with new state objects, with new state
objects overriding existing ones of the same type.

Parameters
----------
state: Iterable[State]
    New state objects to add or replace

Returns
-------
Self
    A new ScopeState with the combined state

## StateContext Objects

```python
@final
class StateContext()
```

Context manager for state within a scope.

Manages state propagation and access within a context. Provides
methods to retrieve state by type and create updated state contexts.
This class is immutable after initialization.

#### check\_state

```python
@classmethod
def check_state(cls,
                state: type[StateType],
                instantiate_defaults: bool = False) -> bool
```

Check if state object is available in the current context.

Verifies if state object of the specified type is available the current context.

Parameters
----------
state: type[StateType]
    The type of state to check

instantiate_defaults: bool = False
    Control if default value should be instantiated during check.

Returns
-------
bool
    True if state is available, otherwise False.

#### state

```python
@classmethod
def state(cls,
          state: type[StateType],
          default: StateType | None = None) -> StateType
```

Get a state object by type from the current context.

Retrieves a state object of the specified type from the current context.
If not found, uses the provided default or attempts to create a new instance.

Parameters
----------
state: type[StateType]
    The type of state to retrieve
default: StateType | None
    Optional default value to use if state not found

Returns
-------
StateType
    The requested state object

Raises
------
MissingContext
    If called outside of a state context
MissingState
    If state not found and default not provided or instantiation fails

#### updated

```python
@classmethod
def updated(cls, state: Iterable[State]) -> Self
```

Create a new StateContext with updated state.

If called within an existing context, inherits and updates that context's state.
If called outside any context, creates a new root context.

Parameters
----------
state: Iterable[State]
    New state objects to add or replace

Returns
-------
Self
    A new StateContext with the combined state

#### \_\_enter\_\_

```python
def __enter__() -> None
```

Enter this state context.

Sets this context's state as the current state in the context.

Raises
------
AssertionError
    If attempting to re-enter an already active context

#### \_\_exit\_\_

```python
def __exit__(exc_type: type[BaseException] | None,
             exc_val: BaseException | None,
             exc_tb: TracebackType | None) -> None
```

Exit this state context.

Restores the previous state context.

Parameters
----------
exc_type: type[BaseException] | None
    Type of exception that caused the exit
exc_val: BaseException | None
    Exception instance that caused the exit
exc_tb: TracebackType | None
    Traceback for the exception

Raises
------
AssertionError
    If the context is not active

# haiway.opentelemetry

# haiway.opentelemetry.observability

## ScopeStore Objects

```python
class ScopeStore()
```

Internal class for storing and managing OpenTelemetry scope data.

This class tracks scope state including its span, meter, logger, and context.
It manages the lifecycle of OpenTelemetry resources for a specific scope,
including recording logs, metrics, events, and maintaining the context hierarchy.

#### \_\_init\_\_

```python
def __init__(identifier: ScopeIdentifier, context: Context, span: Span,
             meter: Meter, logger: Logger) -> None
```

Initialize a new scope store with OpenTelemetry resources.

Parameters
----------
identifier : ScopeIdentifier
    The identifier for this scope
context : Context
    The OpenTelemetry context for this scope
span : Span
    The OpenTelemetry span for this scope
meter : Meter
    The OpenTelemetry meter for recording metrics
logger : Logger
    The OpenTelemetry logger for recording logs

#### exited

```python
@property
def exited() -> bool
```

Check if this scope has been marked as exited.

Returns
-------
bool
    True if the scope has been exited, False otherwise

#### exit

```python
def exit() -> None
```

Mark this scope as exited.

Raises
------
AssertionError
    If the scope has already been exited

#### completed

```python
@property
def completed() -> bool
```

Check if this scope and all its nested scopes are completed.

A scope is considered completed when it has been marked as completed
and all of its nested scopes are also completed.

Returns
-------
bool
    True if the scope and all nested scopes are completed

#### try\_complete

```python
def try_complete() -> bool
```

Try to complete this scope if all conditions are met.

A scope can be completed if:
- It has been exited
- It has not already been completed
- All nested scopes are completed

When completed, the span is ended and the context token is detached.

Returns
-------
bool
    True if the scope was successfully completed, False otherwise

#### record\_log

```python
def record_log(message: str, level: ObservabilityLevel) -> None
```

Record a log message with the specified level.

Creates a LogRecord with the current span context and scope identifiers,
and emits it through the OpenTelemetry logger.

Parameters
----------
message : str
    The log message to record
level : ObservabilityLevel
    The severity level of the log

#### record\_exception

```python
def record_exception(exception: BaseException) -> None
```

Record an exception in the current span.

Parameters
----------
exception : BaseException
    The exception to record

#### record\_event

```python
def record_event(event: str, *,
                 attributes: Mapping[str, ObservabilityAttribute]) -> None
```

Record an event in the current span.

Parameters
----------
event : str
    The name of the event to record
attributes : Mapping[str, ObservabilityAttribute]
    Attributes to attach to the event

#### record\_metric

```python
def record_metric(name: str, *, value: float | int, unit: str | None,
                  attributes: Mapping[str, ObservabilityAttribute]) -> None
```

Record a metric with the given name, value, and attributes.

Creates a counter if one does not already exist for the metric name,
and adds the value to it with the provided attributes.

Parameters
----------
name : str
    The name of the metric to record
value : float | int
    The value to add to the metric
unit : str | None
    The unit of the metric (if any)
attributes : Mapping[str, ObservabilityAttribute]
    Attributes to attach to the metric

#### record\_attributes

```python
def record_attributes(
        attributes: Mapping[str, ObservabilityAttribute]) -> None
```

Record attributes in the current span.

Sets each attribute on the span, skipping None and MISSING values.

Parameters
----------
attributes : Mapping[str, ObservabilityAttribute]
    Attributes to set on the span

## OpenTelemetry Objects

```python
@final
class OpenTelemetry()
```

Integration with OpenTelemetry for distributed tracing, metrics, and logging.

This class provides a bridge between Haiway's observability abstractions and
the OpenTelemetry SDK, enabling distributed tracing, metrics collection, and
structured logging with minimal configuration.

The class must be configured once at application startup using the configure()
class method before it can be used.

#### configure

```python
@classmethod
def configure(cls,
              *,
              service: str,
              version: str,
              environment: str,
              otlp_endpoint: str | None = None,
              insecure: bool = True,
              export_interval_millis: int = 5000,
              attributes: Mapping[str, Any] | None = None) -> type[Self]
```

Configure the OpenTelemetry integration.

This method must be called once at application startup to configure the
OpenTelemetry SDK with the appropriate service information, exporters,
and resource attributes.

Parameters
----------
service : str
    The name of the service
version : str
    The version of the service
environment : str
    The deployment environment (e.g., "production", "staging")
otlp_endpoint : str | None, optional
    The OTLP endpoint URL to export telemetry data to. If None, console
    exporters will be used instead.
insecure : bool, default=True
    Whether to use insecure connections to the OTLP endpoint
export_interval_millis : int, default=5000
    How often to export metrics, in milliseconds
attributes : Mapping[str, Any] | None, optional
    Additional resource attributes to include with all telemetry

Returns
-------
type[Self]
    The OpenTelemetry class, for method chaining

#### observability

```python
@classmethod
def observability(cls,
                  level: ObservabilityLevel = ObservabilityLevel.INFO,
                  *,
                  external_trace_id: str | None = None) -> Observability
```

Create an Observability implementation using OpenTelemetry.

This method creates an Observability implementation that bridges Haiway's
observability abstractions to OpenTelemetry, allowing transparent usage
of OpenTelemetry for distributed tracing, metrics, and logging.

Parameters
----------
level : ObservabilityLevel, default=ObservabilityLevel.INFO
    The minimum observability level to record
external_trace_id : str | None, optional
    External trace ID for distributed tracing context propagation.
    If provided, the root span will be linked to this external trace.

Returns
-------
Observability
    An Observability implementation that uses OpenTelemetry

Notes
-----
The OpenTelemetry class must be configured using configure() before
calling this method.

#### SEVERITY\_MAPPING

Mapping from Haiway ObservabilityLevel to OpenTelemetry SeverityNumber.

# haiway.utils.queue

## AsyncQueue Objects

```python
class AsyncQueue()
```

Asynchronous queue supporting iteration and finishing.

A queue implementation optimized for asynchronous workflows, providing async
iteration over elements and supporting operations like enqueuing elements,
finishing the queue, and cancellation.

Cannot be concurrently consumed by multiple readers - only one consumer
can iterate through the queue at a time.

Parameters
----------
*elements : Element
    Initial elements to populate the queue with
loop : AbstractEventLoop | None, default=None
    Event loop to use for async operations. If None, the running loop is used.

Notes
-----
This class is immutable with respect to its attributes after initialization.
Any attempt to modify its attributes directly will raise an AttributeError.

#### is\_finished

```python
@property
def is_finished() -> bool
```

Check if the queue has been marked as finished.

Returns
-------
bool
    True if the queue has been finished, False otherwise

#### enqueue

```python
def enqueue(element: Element) -> None
```

Add an element to the queue.

If a consumer is waiting for an element, it will be immediately notified.
Otherwise, the element is appended to the queue.

Parameters
----------
element : Element
    The element to add to the queue

Raises
------
RuntimeError
    If the queue has already been finished

#### finish

```python
def finish(exception: BaseException | None = None) -> None
```

Mark the queue as finished, optionally with an exception.

After finishing, no more elements can be enqueued. Any waiting consumers
will be notified with the provided exception or StopAsyncIteration.
If the queue is already finished, this method does nothing.

Parameters
----------
exception : BaseException | None, default=None
    Optional exception to raise in consumers. If None, StopAsyncIteration
    is used to signal normal completion.

#### cancel

```python
def cancel() -> None
```

Cancel the queue with a CancelledError exception.

This is a convenience method that calls finish() with a CancelledError.
Any waiting consumers will receive this exception.

#### clear

```python
def clear() -> None
```

Clear all pending elements from the queue.

This method removes all elements currently in the queue. It will only
clear the queue if no consumer is currently waiting for an element.

# haiway.utils.env

#### getenv

```python
def getenv(key: str,
           mapping: Callable[[str], Value],
           *,
           default: Value | None = None,
           required: bool = False) -> Value | None
```

Get a value from an environment variable and transforms.

Uses provided transformation method to deliver custom data type from env variable.

Parameters
----------
key : str
    The environment variable name to retrieve
mapping : Callable[[str], Value]
    Custom transformation of env value to desired value type.
default : Value | None, optional
    Value to return if the environment variable is not set
required : bool, default=False
    If True and the environment variable is not set and no default is provided,
    raises a ValueError

Returns
-------
Value | None
    The value from the environment variable, or the default value

Raises
------
ValueError
    If required=True, the environment variable is not set, and no default is provided

#### getenv\_bool

```python
def getenv_bool(key: str,
                default: bool | None = None,
                *,
                required: bool = False) -> bool | None
```

Get a boolean value from an environment variable.

Interprets 'true', '1', and 't' (case-insensitive) as True,
any other value as False.

Parameters
----------
key : str
    The environment variable name to retrieve
default : bool | None, optional
    Value to return if the environment variable is not set
required : bool, default=False
    If True and the environment variable is not set and no default is provided,
    raises a ValueError

Returns
-------
bool | None
    The boolean value from the environment variable, or the default value

Raises
------
ValueError
    If required=True, the environment variable is not set, and no default is provided

#### getenv\_int

```python
def getenv_int(key: str,
               default: int | None = None,
               *,
               required: bool = False) -> int | None
```

Get an integer value from an environment variable.

Parameters
----------
key : str
    The environment variable name to retrieve
default : int | None, optional
    Value to return if the environment variable is not set
required : bool, default=False
    If True and the environment variable is not set and no default is provided,
    raises a ValueError

Returns
-------
int | None
    The integer value from the environment variable, or the default value

Raises
------
ValueError
    If the environment variable is set but cannot be converted to an integer,
    or if required=True, the environment variable is not set, and no default is provided

#### getenv\_float

```python
def getenv_float(key: str,
                 default: float | None = None,
                 *,
                 required: bool = False) -> float | None
```

Get a float value from an environment variable.

Parameters
----------
key : str
    The environment variable name to retrieve
default : float | None, optional
    Value to return if the environment variable is not set
required : bool, default=False
    If True and the environment variable is not set and no default is provided,
    raises a ValueError

Returns
-------
float | None
    The float value from the environment variable, or the default value

Raises
------
ValueError
    If the environment variable is set but cannot be converted to a float,
    or if required=True, the environment variable is not set, and no default is provided

#### getenv\_str

```python
def getenv_str(key: str,
               default: str | None = None,
               *,
               required: bool = False) -> str | None
```

Get a string value from an environment variable.

Parameters
----------
key : str
    The environment variable name to retrieve
default : str | None, optional
    Value to return if the environment variable is not set
required : bool, default=False
    If True and the environment variable is not set and no default is provided,
    raises a ValueError

Returns
-------
str | None
    The string value from the environment variable, or the default value

Raises
------
ValueError
    If required=True, the environment variable is not set, and no default is provided

#### getenv\_base64

```python
def getenv_base64(key: str,
                  default: Value | None = None,
                  *,
                  decoder: Callable[[bytes], Value],
                  required: bool = False) -> Value | None
```

Get a base64-encoded value from an environment variable and decode it.

Parameters
----------
key : str
    The environment variable name to retrieve
default : Value | None, optional
    Value to return if the environment variable is not set
decoder : Callable[[bytes], Value]
    Function to decode the base64-decoded bytes into the desired type
required : bool, default=False
    If True and the environment variable is not set and no default is provided,
    raises a ValueError

Returns
-------
Value | None
    The decoded value from the environment variable, or the default value

Raises
------
ValueError
    If required=True, the environment variable is not set, and no default is provided

#### load\_env

```python
def load_env(path: str | None = None, override: bool = True) -> None
```

Load environment variables from a .env file.

A minimalist implementation that reads key-value pairs from the specified file
and sets them as environment variables. If the file doesn't exist, the function
silently continues without loading any variables.

The file format follows these rules:
- Lines starting with '#' are treated as comments and ignored
- Each variable must be on a separate line in the format `KEY=VALUE`
- No spaces or additional characters are allowed around the '=' sign
- Keys without values are ignored
- Inline comments are not supported

Parameters
----------
path : str | None, default=None
    Path to the environment file. If None, defaults to '.env'
override : bool, default=True
    If True, overrides existing environment variables with values from the file.
    If False, only sets variables that don't already exist in the environment.

Returns
-------
None
    This function modifies the environment variables but doesn't return anything.

# haiway.utils.always

#### always

```python
def always(value: Value) -> Callable[..., Value]
```

Factory method creating functions returning always the same value.

Parameters
----------
value: Value
    value to be always returned from prepared function

Returns
-------
Callable[..., Value]
    function ignoring arguments and always returning the provided value.

#### async\_always

```python
def async_always(value: Value) -> Callable[..., Coroutine[Any, Any, Value]]
```

Factory method creating async functions returning always the same value.

Parameters
----------
value: Value
    value to be always returned from prepared function

Returns
-------
Callable[..., Coroutine[Any, Any, Value]]
    async function ignoring arguments and always returning the provided value.

# haiway.utils

# haiway.utils.formatting

#### format\_str

```python
def format_str(value: Any) -> str
```

Format any Python value into a readable string representation.

Creates a human-readable string representation of complex data structures,
with proper indentation and formatting for nested structures. This is especially
useful for logging, debugging, and observability contexts.

Parameters
----------
value : Any
    The value to format as a string

Returns
-------
str
    A formatted string representation of the input value

Notes
-----
- Strings are quoted, with multi-line strings using triple quotes
- Bytes are prefixed with 'b' and quoted
- Mappings (like dictionaries) are formatted with keys and values
- Sequences (like lists) are formatted with indices and values
- Objects are formatted with their attribute names and values
- MISSING values are converted to empty strings
- Nested structures maintain proper indentation

# haiway.utils.stream

## AsyncStream Objects

```python
class AsyncStream()
```

An asynchronous stream implementation supporting push-based async iteration.

AsyncStream provides a way to create a stream of elements where producers can
asynchronously send elements and consumers can iterate over them using async
iteration. Only one consumer can iterate through the stream at a time.

This class implements a flow-controlled stream where the producer waits until
the consumer is ready to receive the next element, ensuring back-pressure.

Unlike AsyncQueue, AsyncStream cannot be reused for multiple iterations and
requires coordination between producer and consumer.

#### \_\_init\_\_

```python
def __init__(loop: AbstractEventLoop | None = None) -> None
```

Initialize a new asynchronous stream.

Parameters
----------
loop : AbstractEventLoop | None, default=None
    Event loop to use for async operations. If None, the running loop is used.

#### finished

```python
@property
def finished() -> bool
```

Check if the stream has been marked as finished.

Returns
-------
bool
    True if the stream has been finished, False otherwise

#### send

```python
async def send(element: Element) -> None
```

Send an element to the stream.

This method waits until the consumer is ready to receive the element,
implementing back-pressure. If the stream is finished, the element will
be silently discarded.

Parameters
----------
element : Element
    The element to send to the stream

#### finish

```python
def finish(exception: BaseException | None = None) -> None
```

Mark the stream as finished, optionally with an exception.

After finishing, sent elements will be silently discarded. The consumer
will receive the provided exception or StopAsyncIteration when attempting
to get the next element.

If the stream is already finished, this method does nothing.

Parameters
----------
exception : BaseException | None, default=None
    Optional exception to raise in the consumer. If None, StopAsyncIteration
    is used to signal normal completion.

#### cancel

```python
def cancel() -> None
```

Cancel the stream with a CancelledError exception.

This is a convenience method that calls finish() with a CancelledError.
The consumer will receive this exception when attempting to get the next element.

#### \_\_anext\_\_

```python
async def __anext__() -> Element
```

Get the next element from the stream.

This method is called automatically when the stream is used in an
async for loop. It waits for the next element to be sent or for
the stream to be finished.

Returns
-------
Element
    The next element from the stream

Raises
------
BaseException
    The exception provided to finish(), or StopAsyncIteration if
    finish() was called without an exception
AssertionError
    If the stream is being consumed by multiple consumers

# haiway.utils.mimic

# haiway.utils.collections

#### as\_list

```python
def as_list(collection: Iterable[T] | None) -> list[T] | None
```

Converts any given Iterable into a list.

Parameters
----------
collection : Iterable[T] | None
    The input collection to be converted to a list.
    If None is provided, None is returned.

Returns
-------
list[T] | None
    A new list containing all elements of the input collection,
    or the original list if it was already one.
    Returns None if None was provided.

#### as\_tuple

```python
def as_tuple(collection: Iterable[T] | None) -> tuple[T, ...] | None
```

Converts any given Iterable into a tuple.

Parameters
----------
collection : Iterable[T] | None
    The input collection to be converted to a tuple.
    If None is provided, None is returned.

Returns
-------
tuple[T, ...] | None
    A new tuple containing all elements of the input collection,
    or the original tuple if it was already one.
    Returns None if None was provided.

#### as\_set

```python
def as_set(collection: Set[T] | None) -> set[T] | None
```

Converts any given Set into a set.

Parameters
----------
collection : Set[T] | None
    The input collection to be converted to a set.
    If None is provided, None is returned.

Returns
-------
set[T] | None
    A new set containing all elements of the input collection,
    or the original set if it was already one.
    Returns None if None was provided.

#### as\_dict

```python
def as_dict(collection: Mapping[K, V] | None) -> dict[K, V] | None
```

Converts any given Mapping into a dict.

Parameters
----------
collection : Mapping[K, V] | None
    The input collection to be converted to a dict.
    If None is provided, None is returned.

Returns
-------
dict[K, V] | None
    A new dict containing all elements of the input collection,
    or the original dict if it was already one.
    Returns None if None was provided.

#### without\_missing

```python
def without_missing(mapping: Mapping[str, Any],
                    *,
                    typed: type[T] | None = None) -> T | Mapping[str, Any]
```

Create a new mapping without any items that have MISSING values.

Parameters
----------
mapping : Mapping[str, Any]
    The input mapping to be filtered.
typed : type[T] | None, default=None
    Optional type to cast the result to. If provided, the result will be
    cast to this type before returning.

Returns
-------
T | Mapping[str, Any]
    A new mapping containing all items of the input mapping,
    except items with MISSING values. If typed is provided,
    the result is cast to that type.

# haiway.utils.logs

#### setup\_logging

```python
def setup_logging(
    *loggers: str,
    time: bool = True,
    debug: bool = getenv_bool("DEBUG_LOGGING", __debug__)
) -> None
```

Setup logging configuration and prepare specified loggers.

Parameters
----------
*loggers: str
    names of additional loggers to configure.
time: bool = True
    include timestamps in logs.
debug: bool = __debug__
    include debug logs.

NOTE: this function should be run only once on application start

# haiway.utils.noop

#### noop

```python
def noop(*args: Any, **kwargs: Any) -> None
```

Placeholder function that accepts any arguments and does nothing.

This utility function is useful for cases where a callback is required
but no action should be taken, such as in testing, as a default handler,
or as a placeholder during development.

Parameters
----------
*args: Any
    Any positional arguments, which are ignored
**kwargs: Any
    Any keyword arguments, which are ignored

Returns
-------
None
    This function performs no operation and returns nothing

#### async\_noop

```python
async def async_noop(*args: Any, **kwargs: Any) -> None
```

Asynchronous placeholder function that accepts any arguments and does nothing.

This utility function is useful for cases where an async callback is required
but no action should be taken, such as in testing, as a default async handler,
or as a placeholder during asynchronous workflow development.

Parameters
----------
*args: Any
    Any positional arguments, which are ignored
**kwargs: Any
    Any keyword arguments, which are ignored

Returns
-------
None
    This function performs no operation and returns nothing

# haiway.state.requirement

## AttributeRequirement Objects

```python
@final
class AttributeRequirement()
```

Represents a requirement or constraint on an attribute value.

This class provides a way to define and check constraints on attribute values
within State objects. It supports various comparison operations like equality,
containment, and logical combinations of requirements.

The class is generic over the Root type, which is the type of object that
contains the attribute being constrained.

Requirements can be combined using logical operators:
- & (AND): Both requirements must be met
- | (OR): At least one requirement must be met

#### equal

```python
@classmethod
def equal(cls, value: Parameter,
          path: AttributePath[Root, Parameter] | Parameter) -> Self
```

Create a requirement that an attribute equals a specific value.

Parameters
----------
value : Parameter
    The value to check equality against
path : AttributePath[Root, Parameter] | Parameter
    The path to the attribute to check

Returns
-------
Self
    A new requirement instance

Raises
------
AssertionError
    If path is not an AttributePath

#### text\_match

```python
@classmethod
def text_match(cls, value: str, path: AttributePath[Root, str] | str) -> Self
```

Create a requirement that performs text matching on an attribute.

Parameters
----------
value : str
    The search term (can contain multiple words separated by spaces/punctuation)
path : AttributePath[Root, str] | str
    The path to the string attribute to search in

Returns
-------
Self
    A new requirement instance

Raises
------
AssertionError
    If path is not an AttributePath

#### not\_equal

```python
@classmethod
def not_equal(cls, value: Parameter,
              path: AttributePath[Root, Parameter] | Parameter) -> Self
```

Create a requirement that an attribute does not equal a specific value.

Parameters
----------
value : Parameter
    The value to check inequality against
path : AttributePath[Root, Parameter] | Parameter
    The path to the attribute to check

Returns
-------
Self
    A new requirement instance

Raises
------
AssertionError
    If path is not an AttributePath

#### contains

```python
@classmethod
def contains(
    cls, value: Parameter, path: AttributePath[
        Root,
        Collection[Parameter] | tuple[Parameter, ...] | list[Parameter]
        | set[Parameter],
    ]
    | Collection[Parameter]
    | tuple[Parameter, ...]
    | list[Parameter]
    | set[Parameter]
) -> Self
```

Create a requirement that a collection attribute contains a specific value.

Parameters
----------
value : Parameter
    The value that should be contained in the collection
path : AttributePath[Root, Collection[Parameter] | ...] | Collection[Parameter] | ...
    The path to the collection attribute to check

Returns
-------
Self
    A new requirement instance

Raises
------
AssertionError
    If path is not an AttributePath

#### contains\_any

```python
@classmethod
def contains_any(
    cls, value: Collection[Parameter], path: AttributePath[
        Root,
        Collection[Parameter] | tuple[Parameter, ...] | list[Parameter]
        | set[Parameter],
    ]
    | Collection[Parameter]
    | tuple[Parameter, ...]
    | list[Parameter]
    | set[Parameter]
) -> Self
```

Create a requirement that a collection attribute contains any of the specified values.

Parameters
----------
value : Collection[Parameter]
    The collection of values, any of which should be contained
path : AttributePath[Root, Collection[Parameter] | ...] | Collection[Parameter] | ...
    The path to the collection attribute to check

Returns
-------
Self
    A new requirement instance

Raises
------
AssertionError
    If path is not an AttributePath

#### contained\_in

```python
@classmethod
def contained_in(cls, value: Collection[Parameter],
                 path: AttributePath[Root, Parameter] | Parameter) -> Self
```

Create a requirement that an attribute value is contained in a specific collection.

Parameters
----------
value : Collection[Parameter]
    The collection that should contain the attribute value
path : AttributePath[Root, Parameter] | Parameter
    The path to the attribute to check

Returns
-------
Self
    A new requirement instance

Raises
------
AssertionError
    If path is not an AttributePath

#### \_\_init\_\_

```python
def __init__(lhs: Any, operator: Literal[
    "equal",
    "text_match",
    "not_equal",
    "contains",
    "contains_any",
    "contained_in",
    "and",
    "or",
], rhs: Any, check: Callable[[Root], None]) -> None
```

Initialize a new attribute requirement.

Parameters
----------
lhs : Any
    The left-hand side of the requirement (typically a path or value)
operator : Literal["equal", "not_equal", "contains", "contains_any", "contained_in", "and", "or"]
    The operator that defines the type of requirement
rhs : Any
    The right-hand side of the requirement (typically a value or path)
check : Callable[[Root], None]
    A function that validates the requirement, raising ValueError if not met

#### \_\_and\_\_

```python
def __and__(other: Self) -> Self
```

Combine this requirement with another using logical AND.

Creates a new requirement that is satisfied only if both this requirement
and the other requirement are satisfied.

Parameters
----------
other : Self
    Another requirement to combine with this one

Returns
-------
Self
    A new requirement representing the logical AND of both requirements

#### \_\_or\_\_

```python
def __or__(other: Self) -> Self
```

Combine this requirement with another using logical OR.

Creates a new requirement that is satisfied if either this requirement
or the other requirement is satisfied.

Parameters
----------
other : Self
    Another requirement to combine with this one

Returns
-------
Self
    A new requirement representing the logical OR of both requirements

#### check

```python
def check(root: Root, *, raise_exception: bool = True) -> bool
```

Check if the requirement is satisfied by the given root object.

Parameters
----------
root : Root
    The object to check the requirement against
raise_exception : bool, default=True
    If True, raises an exception when the requirement is not met

Returns
-------
bool
    True if the requirement is satisfied, False otherwise

Raises
------
ValueError
    If the requirement is not satisfied and raise_exception is True

#### filter

```python
def filter(values: Iterable[Root]) -> list[Root]
```

Filter an iterable of values, keeping only those that satisfy this requirement.

Parameters
----------
values : Iterable[Root]
    The values to filter

Returns
-------
list[Root]
    A list containing only the values that satisfy this requirement

# haiway.state.attributes

## AttributeAnnotation Objects

```python
@final
class AttributeAnnotation()
```

Represents a type annotation for a State attribute with additional metadata.

This class encapsulates information about a type annotation, including its
origin type, type arguments, whether it's required, and any extra metadata.
It's used internally by the State system to track and validate attribute types.

#### \_\_init\_\_

```python
def __init__(*,
             origin: Any,
             arguments: Sequence[Any] | None = None,
             required: bool = True,
             extra: Mapping[str, Any] | None = None) -> None
```

Initialize a new attribute annotation.

Parameters
----------
origin : Any
    The base type of the annotation (e.g., str, int, List)
arguments : Sequence[Any] | None
    Type arguments for generic types (e.g., T in List[T])
required : bool
    Whether this attribute is required (cannot be omitted)
extra : Mapping[str, Any] | None
    Additional metadata about the annotation

#### update\_required

```python
def update_required(required: bool) -> Self
```

Update the required flag for this annotation.

The resulting required flag is the logical AND of the current
flag and the provided value.

Parameters
----------
required : bool
    New required flag value to combine with the existing one

Returns
-------
Self
    This annotation with the updated required flag

#### \_\_str\_\_

```python
def __str__() -> str
```

Convert this annotation to a string representation.

Returns a readable string representation of the type, including
its origin type and any type arguments.

Returns
-------
str
    String representation of this annotation

#### attribute\_annotations

```python
def attribute_annotations(
        cls: type[Any],
        type_parameters: Mapping[str,
                                 Any]) -> Mapping[str, AttributeAnnotation]
```

Extract and process type annotations from a class.

This function analyzes a class's type hints and converts them to AttributeAnnotation
objects, which provide rich type information used by the State system for validation
and other type-related operations.

Parameters
----------
cls : type[Any]
    The class to extract annotations from
type_parameters : Mapping[str, Any]
    Type parameters to substitute in generic type annotations

Returns
-------
Mapping[str, AttributeAnnotation]
    A mapping of attribute names to their processed type annotations

Notes
-----
Private attributes (prefixed with underscore) and ClassVars are ignored.

#### resolve\_attribute\_annotation

```python
def resolve_attribute_annotation(
    annotation: Any, module: str, type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation]
) -> AttributeAnnotation
```

Resolve a Python type annotation into an AttributeAnnotation object.

This function analyzes any Python type annotation and converts it into
an AttributeAnnotation that captures its structure, including handling
for special types like unions, optionals, literals, generics, etc.

Parameters
----------
annotation : Any
    The type annotation to resolve
module : str
    The module where the annotation is defined (for resolving ForwardRefs)
type_parameters : Mapping[str, Any]
    Type parameters to substitute in generic type annotations
self_annotation : AttributeAnnotation | None
    The annotation for Self references, if available
recursion_guard : MutableMapping[str, AttributeAnnotation]
    Cache to prevent infinite recursion for recursive types

Returns
-------
AttributeAnnotation
    A resolved AttributeAnnotation representing the input annotation

Raises
------
RuntimeError
    If a Self annotation is used but self_annotation is not provided
TypeError
    If the annotation is of an unsupported type

# haiway.state

# haiway.state.structure

## StateAttribute Objects

```python
@final
class StateAttribute()
```

Represents an attribute in a State class with its metadata.

This class holds information about a specific attribute in a State class,
including its name, type annotation, default value, and validation rules.
It is used internally by the State metaclass to manage state attributes
and ensure their immutability and type safety.

#### \_\_init\_\_

```python
def __init__(name: str, annotation: AttributeAnnotation,
             default: DefaultValue[Value],
             validator: AttributeValidation[Value]) -> None
```

Initialize a new StateAttribute.

Parameters
----------
name : str
    The name of the attribute
annotation : AttributeAnnotation
    The type annotation of the attribute
default : DefaultValue[Value]
    The default value provider for the attribute
validator : AttributeValidation[Value]
    The validation function for the attribute values

#### validated

```python
def validated(value: Any | Missing) -> Value
```

Validate and potentially transform the provided value.

If the value is MISSING, the default value is used instead.
The value (or default) is then passed through the validator.

Parameters
----------
value : Any | Missing
    The value to validate, or MISSING to use the default

Returns
-------
Value
    The validated and potentially transformed value

## StateMeta Objects

```python
@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
    field_specifiers=(DefaultValue,),
)
class StateMeta(type)
```

Metaclass for State classes that manages attribute definitions and validation.

This metaclass is responsible for:
- Processing attribute annotations and defaults
- Creating StateAttribute instances for each attribute
- Setting up validation for attributes
- Managing generic type parameters and specialization
- Creating immutable class instances

The dataclass_transform decorator allows State classes to be treated
like dataclasses by static type checkers while using custom initialization
and validation logic.

#### \_\_new\_\_

```python
def __new__(cls,
            name: str,
            bases: tuple[type, ...],
            namespace: dict[str, Any],
            type_parameters: dict[str, Any] | None = None,
            **kwargs: Any) -> Any
```

Create a new State class with processed attributes and validation.

Parameters
----------
name : str
    The name of the new class
bases : tuple[type, ...]
    The base classes
namespace : dict[str, Any]
    The class namespace (attributes and methods)
type_parameters : dict[str, Any] | None
    Type parameters for generic specialization
**kwargs : Any
    Additional arguments for class creation

Returns
-------
Any
    The new class object

#### validator

```python
def validator(cls, value: Any) -> Any
```

Placeholder for the validator method that will be implemented in each State class.

This method validates and potentially transforms a value to ensure it
conforms to the class's requirements.

Parameters
----------
value : Any
    The value to validate

Returns
-------
Any
    The validated value

#### \_\_instancecheck\_\_

```python
def __instancecheck__(instance: Any) -> bool
```

Check if an instance is an instance of this class.

Implements isinstance() behavior for State classes, with special handling
for generic type parameters and validation.

Parameters
----------
instance : Any
    The instance to check

Returns
-------
bool
    True if the instance is an instance of this class, False otherwise

#### \_\_subclasscheck\_\_

```python
def __subclasscheck__(subclass: type[Any]) -> bool
```

Check if a class is a subclass of this class.

Implements issubclass() behavior for State classes, with special handling
for generic type parameters.

Parameters
----------
subclass : type[Any]
    The class to check

Returns
-------
bool
    True if the class is a subclass of this class, False otherwise

Raises
------
RuntimeError
    If there is an issue with type parametrization

## State Objects

```python
class State(metaclass=StateMeta)
```

Base class for immutable data structures.

State provides a framework for creating immutable, type-safe data classes
with validation. It's designed to represent application state that can be
safely shared and updated in a predictable manner.

Key features:
- Immutable: Instances cannot be modified after creation
- Type-safe: Attributes are validated based on type annotations
- Generic: Can be parameterized with type variables
- Declarative: Uses a class-based declaration syntax similar to dataclasses
- Validated: Custom validation rules can be applied to attributes

State classes can be created by subclassing State and declaring attributes:

```python
class User(State):
    name: str
    age: int
    email: str | None = None
```

Instances are created using standard constructor syntax:

```python
user = User(name="Alice", age=30)
```

New instances with updated values can be created from existing ones:

```python
updated_user = user.updated(age=31)
```

Path-based updates are also supported:

```python
updated_user = user.updating(User._.age, 31)
```

#### \_\_class\_getitem\_\_

```python
@classmethod
def __class_getitem__(
        cls, type_argument: tuple[type[Any], ...] | type[Any]) -> type[Self]
```

Create a specialized version of a generic State class.

This method enables the generic type syntax Class[TypeArg] for State classes.

Parameters
----------
type_argument : tuple[type[Any], ...] | type[Any]
    The type arguments to specialize the class with

Returns
-------
type[Self]
    A specialized version of the class

Raises
------
AssertionError
    If the class is not generic or is already specialized,
    or if the number of type arguments doesn't match the parameters

#### validator

```python
@classmethod
def validator(cls, value: Any) -> Self
```

Validate and convert a value to an instance of this class.

Parameters
----------
value : Any
    The value to validate and convert

Returns
-------
Self
    An instance of this class

Raises
------
TypeError
    If the value cannot be converted to an instance of this class

#### \_\_init\_\_

```python
def __init__(**kwargs: Any) -> None
```

Initialize a new State instance.

Creates a new instance with the provided attribute values.
Attributes not specified will use their default values.
All attributes are validated according to their type annotations.

Parameters
----------
**kwargs : Any
    Attribute values for the new instance

Raises
------
Exception
    If validation fails for any attribute

#### updating

```python
def updating(path: AttributePath[Self, Value] | Value, value: Value) -> Self
```

Create a new instance with an updated value at the specified path.

Parameters
----------
path : AttributePath[Self, Value] | Value
    An attribute path created with Class._.attribute syntax
value : Value
    The new value for the specified attribute

Returns
-------
Self
    A new instance with the updated value

Raises
------
AssertionError
    If path is not an AttributePath

#### updated

```python
def updated(**kwargs: Any) -> Self
```

Create a new instance with updated attribute values.

This method creates a new instance with the same attribute values as this
instance, but with any provided values updated.

Parameters
----------
**kwargs : Any
    New values for attributes to update

Returns
-------
Self
    A new instance with updated values

#### to\_str

```python
def to_str() -> str
```

Convert this instance to a string representation.

Returns
-------
str
    A string representation of this instance

#### to\_mapping

```python
def to_mapping(recursive: bool = False) -> Mapping[str, Any]
```

Convert this instance to a mapping of attribute names to values.

Parameters
----------
recursive : bool, default=False
    If True, nested State instances are also converted to mappings

Returns
-------
Mapping[str, Any]
    A mapping of attribute names to values

#### \_\_str\_\_

```python
def __str__() -> str
```

Get a string representation of this instance.

Returns
-------
str
    A string representation in the format "ClassName(attr1: value1, attr2: value2)"

#### \_\_eq\_\_

```python
def __eq__(other: Any) -> bool
```

Check if this instance is equal to another object.

Two State instances are considered equal if they are instances of the
same class or subclass and have equal values for all attributes.

Parameters
----------
other : Any
    The object to compare with

Returns
-------
bool
    True if the objects are equal, False otherwise

#### \_\_copy\_\_

```python
def __copy__() -> Self
```

Create a shallow copy of this instance.

Since State is immutable, this returns the instance itself.

Returns
-------
Self
    This instance

#### \_\_deepcopy\_\_

```python
def __deepcopy__(memo: dict[int, Any] | None) -> Self
```

Create a deep copy of this instance.

Since State is immutable, this returns the instance itself.

Parameters
----------
memo : dict[int, Any] | None
    Memoization dictionary for already copied objects

Returns
-------
Self
    This instance

#### \_\_replace\_\_

```python
def __replace__(**kwargs: Any) -> Self
```

Create a new instance with replaced attribute values.

This internal method is used by updated() to create a new instance
with updated values.

Parameters
----------
**kwargs : Any
    New values for attributes to replace

Returns
-------
Self
    A new instance with replaced values

# haiway.state.path

## AttributePathComponent Objects

```python
class AttributePathComponent(ABC)
```

Abstract base class for components in an attribute path.

This class defines the interface for components that make up an attribute path,
such as property access, sequence item access, or mapping item access.
Each component knows how to access and update values at its position in the path.

#### path\_str

```python
@abstractmethod
def path_str(current: str | None = None) -> str
```

Convert this path component to a string representation.

Parameters
----------
current : str | None
    The current path string to append to

Returns
-------
str
    String representation of the path including this component

#### access

```python
@abstractmethod
def access(subject: Any) -> Any
```

Access the property value from the subject.

Parameters
----------
subject : Any
    The object to access the property from

Returns
-------
Any
    The value of the property

Raises
------
AttributeError
    If the property doesn't exist on the subject

#### assigning

```python
@abstractmethod
def assigning(subject: Any, value: Any) -> Any
```

Create a new object with an updated value at this path component.

Parameters
----------
subject : Any
    The original object to update
value : Any
    The new value to assign at this path component

Returns
-------
Any
    A new object with the updated value

Raises
------
TypeError
    If the subject cannot be updated with the given value

## PropertyAttributePathComponent Objects

```python
@final
class PropertyAttributePathComponent(AttributePathComponent)
```

#### path\_str

```python
def path_str(current: str | None = None) -> str
```

Convert this property component to a string representation.

Parameters
----------
current : str | None
    The current path string to append to

Returns
-------
str
    String representation with the property appended

#### access

```python
def access(subject: Any) -> Any
```

Access the property value from the subject.

Parameters
----------
subject : Any
    The object to access the property from

Returns
-------
Any
    The value of the property

#### assigning

```python
def assigning(subject: Any, value: Any) -> Any
```

Create a new subject with an updated property value.

Parameters
----------
subject : Any
    The original object
value : Any
    The new value for the property

Returns
-------
Any
    A new object with the updated property value

Raises
------
TypeError
    If the subject doesn't support property updates

## SequenceItemAttributePathComponent Objects

```python
@final
class SequenceItemAttributePathComponent()
```

Path component for accessing items in a sequence by index.

This component represents sequence item access using index notation (seq[index])
in an attribute path. It provides type-safe access and updates for sequence items.

#### path\_str

```python
def path_str(current: str | None = None) -> str
```

Convert this sequence item component to a string representation.

Parameters
----------
current : str | None
    The current path string to append to

Returns
-------
str
    String representation with the sequence index appended

#### access

```python
def access(subject: Any) -> Any
```

Access the sequence item from the subject.

Parameters
----------
subject : Any
    The sequence to access the item from

Returns
-------
Any
    The value at the specified index

Raises
------
IndexError
    If the index is out of bounds

#### assigning

```python
def assigning(subject: Any, value: Any) -> Any
```

Create a new sequence with an updated item value.

Parameters
----------
subject : Any
    The original sequence
value : Any
    The new value for the item

Returns
-------
Any
    A new sequence with the updated item

Raises
------
TypeError
    If the subject doesn't support item updates

## MappingItemAttributePathComponent Objects

```python
@final
class MappingItemAttributePathComponent(AttributePathComponent)
```

#### path\_str

```python
def path_str(current: str | None = None) -> str
```

Convert this mapping item component to a string representation.

Parameters
----------
current : str | None
    The current path string to append to

Returns
-------
str
    String representation with the mapping key appended

#### access

```python
def access(subject: Any) -> Any
```

Access the mapping item from the subject.

Parameters
----------
subject : Any
    The mapping to access the item from

Returns
-------
Any
    The value associated with the key

Raises
------
KeyError
    If the key doesn't exist in the mapping

#### assigning

```python
def assigning(subject: Any, value: Any) -> Any
```

Create a new mapping with an updated item value.

Parameters
----------
subject : Any
    The original mapping
value : Any
    The new value for the item

Returns
-------
Any
    A new mapping with the updated item

Raises
------
TypeError
    If the subject doesn't support item updates

## AttributePath Objects

```python
@final
class AttributePath()
```

Represents a path to an attribute within a nested structure.

AttributePath enables type-safe attribute access and updates for complex
nested structures, particularly State objects. It provides a fluent interface
for building paths using attribute access (obj.attr) and item access (obj[key])
syntax.

The class is generic over two type parameters:
- Root: The type of the root object the path starts from
- Attribute: The type of the attribute the path points to

AttributePaths are immutable and can be reused. When applied to different
root objects, they will access the same nested path in each object.

Examples
--------
Creating paths:
```python
# Access user.name
User._.name

# Access users[0].address.city
User._.users[0].address.city

# Access data["key"]
Data._["key"]
```

Using paths:
```python
# Get value
name = User._.name(user)

# Update value
updated_user = user.updating(User._.name, "New Name")
```

#### \_\_init\_\_

```python
@overload
def __init__(root: type[Root], *components: AttributePathComponent,
             attribute: type[Attribute]) -> None
```

Initialize a new attribute path.

Parameters
----------
root : type[Root]
    The root type this path starts from
*components : AttributePathComponent
    Path components defining the traversal from root to attribute
attribute : type[Attribute]
    The type of the attribute at the end of this path

Raises
------
AssertionError
    If no components are provided and root != attribute

#### components

```python
@property
def components() -> Sequence[str]
```

Get the components of this path as strings.

Returns
-------
Sequence[str]
    String representations of each path component

#### \_\_str\_\_

```python
def __str__() -> str
```

Get a string representation of this path.

The string starts empty and builds up by appending each component.

Returns
-------
str
    A string representation of the path (e.g., ".attr1.attr2[0]")

#### \_\_repr\_\_

```python
def __repr__() -> str
```

Get a detailed string representation of this path.

Unlike __str__, this includes the root type name at the beginning.

Returns
-------
str
    A detailed string representation of the path (e.g., "User.name[0]")

#### \_\_getattr\_\_

```python
def __getattr__(name: str) -> Any
```

Extend the path with property access to the specified attribute.

This method is called when using dot notation (path.attribute) on an
AttributePath instance. It creates a new AttributePath that includes
the additional property access.

Parameters
----------
name : str
    The attribute name to access

Returns
-------
AttributePath
    A new AttributePath extended with the attribute access

Raises
------
AttributeError
    If the attribute is not found or cannot be accessed

#### \_\_getitem\_\_

```python
def __getitem__(key: str | int) -> Any
```

Extend the path with item access using the specified key.

This method is called when using item access notation (path[key]) on an
AttributePath instance. It creates a new AttributePath that includes the
additional item access component.

Parameters
----------
key : str | int
    The key or index to access. String keys are used for mapping access
    and integer keys for sequence/tuple access.

Returns
-------
AttributePath
    A new AttributePath extended with the item access component

Raises
------
TypeError
    If the key type is incompatible with the attribute type or if the
    attribute type does not support item access

#### \_\_call\_\_

```python
@overload
def __call__(source: Root) -> Attribute
```

Access the attribute value at this path in the source object.

This overload is used when retrieving a value without updating it.

Parameters
----------
source : Root
    The source object to access the attribute in

Returns
-------
Attribute
    The attribute value at this path

Raises
------
AttributeError
    If any component in the path doesn't exist
TypeError
    If any component in the path is of the wrong type

#### \_\_call\_\_

```python
@overload
def __call__(source: Root, updated: Attribute) -> Root
```

Create a new root object with an updated attribute value at this path.

This overload is used when updating a value.

Parameters
----------
source : Root
    The source object to update
updated : Attribute
    The new value to set at this path

Returns
-------
Root
    A new root object with the updated attribute value

Raises
------
AttributeError
    If any component in the path doesn't exist
TypeError
    If any component in the path is of the wrong type

# haiway.state.validation

## AttributeValidation Objects

```python
class AttributeValidation()
```

Protocol defining the interface for attribute validation functions.

These functions validate and potentially transform input values to
ensure they conform to the expected type or format.

## AttributeValidationError Objects

```python
class AttributeValidationError(Exception)
```

Exception raised when attribute validation fails.

This exception indicates that a value failed to meet the
validation requirements for an attribute.

## AttributeValidator Objects

```python
@final
class AttributeValidator()
```

Creates and manages validation functions for attribute types.

This class is responsible for creating appropriate validation functions
based on type annotations. It handles various types including primitives,
containers, unions, and custom types like State classes.

#### of

```python
@classmethod
def of(
    cls, annotation: AttributeAnnotation, *,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]]
) -> AttributeValidation[Any]
```

Create a validation function for the given type annotation.

This method analyzes the type annotation and creates an appropriate
validation function that can validate and transform values to match
the expected type.

Parameters
----------
annotation : AttributeAnnotation
    The type annotation to create a validator for
recursion_guard : MutableMapping[str, AttributeValidation[Any]]
    A mapping used to detect and handle recursive types

Returns
-------
AttributeValidation[Any]
    A validation function for the given type

Raises
------
TypeError
    If the annotation represents an unsupported type

#### \_\_init\_\_

```python
def __init__(annotation: AttributeAnnotation,
             validation: AttributeValidation[Type] | Missing) -> None
```

Initialize a new attribute validator.

Parameters
----------
annotation : AttributeAnnotation
    The type annotation this validator is for
validation : AttributeValidation[Type] | Missing
    The validation function, or MISSING if not yet set

#### \_\_call\_\_

```python
def __call__(value: Any) -> Any
```

Validate a value against this validator's type annotation.

Parameters
----------
value : Any
    The value to validate

Returns
-------
Any
    The validated and potentially transformed value

Raises
------
AssertionError
    If the validation function is not set
Exception
    If validation fails

# haiway.helpers.retries

#### retry

```python
@overload
def retry(function: Callable[Args, Result]) -> Callable[Args, Result]
```

Function wrapper retrying the wrapped function again on fail.     Works for both sync and async functions.     It is not allowed to be used on class methods.     This wrapper is not thread safe.

Parameters
----------
function: Callable[_Args_T, _Result_T]
    function to wrap in auto retry, either sync or async.

Returns
-------
Callable[_Args_T, _Result_T]
    provided function wrapped in auto retry with default configuration.

#### retry

```python
@overload
def retry(
    *,
    limit: int = 1,
    delay: Callable[[int, Exception], float] | float | None = None,
    catching: set[type[Exception]] | tuple[type[Exception], ...]
    | type[Exception] = Exception
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]]
```

Function wrapper retrying the wrapped function again on fail.     Works for both sync and async functions.     It is not allowed to be used on class methods.     This wrapper is not thread safe.

Parameters
----------
limit: int
    limit of retries, default is 1
delay: Callable[[int, Exception], float] | float | None
    retry delay time in seconds, either concrete value or a function producing it,         default is None (no delay)
catching: set[type[Exception]] | type[Exception] | None
    Exception types that are triggering auto retry. Retry will trigger only when         exceptions of matching types (including subclasses) will occur. CancelledError         will be always propagated even if specified explicitly.
    Default is Exception - all subclasses of Exception will be handled.

Returns
-------
Callable[[Callable[_Args_T, _Result_T]], Callable[_Args_T, _Result_T]]
    function wrapper for adding auto retry

#### retry

```python
def retry(
    function: Callable[Args, Result] | None = None,
    *,
    limit: int = 1,
    delay: Callable[[int, Exception], float] | float | None = None,
    catching: set[type[Exception]] | tuple[type[Exception], ...]
    | type[Exception] = Exception
) -> Callable[[Callable[Args, Result]],
              Callable[Args, Result]] | Callable[Args, Result]
```

Automatically retry a function on failure.

This decorator attempts to execute a function and, if it fails with a specified
exception type, retries the execution up to a configurable number of times,
with an optional delay between attempts.

Can be used as a simple decorator (@retry) or with configuration
parameters (@retry(limit=3, delay=1.0)).

Parameters
----------
function: Callable[Args, Result] | None
    The function to wrap with retry logic. When used as a simple decorator,
    this parameter is provided automatically.
limit: int
    Maximum number of retry attempts. Default is 1, meaning the function
    will be called at most twice (initial attempt + 1 retry).
delay: Callable[[int, Exception], float] | float | None
    Delay between retry attempts in seconds. Can be:
      - None: No delay between retries (default)
      - float: Fixed delay in seconds
      - Callable: A function that calculates delay based on attempt number
        and the caught exception, allowing for backoff strategies
catching: set[type[Exception]] | tuple[type[Exception], ...] | type[Exception]
    Exception types that should trigger retry. Can be a single exception type,
    a set, or a tuple of exception types. Default is Exception (all exception
    types except for CancelledError, which is always propagated).

Returns
-------
Callable
    When used as @retry: Returns the wrapped function with retry logic.
    When used as @retry(...): Returns a decorator that can be applied to a function.

Notes
-----
- Works with both synchronous and asynchronous functions.
- Not thread-safe; concurrent invocations are not coordinated.
- Cannot be used on class methods.
- Always propagates asyncio.CancelledError regardless of catching parameter.
- The function preserves the original function's signature, docstring, and other attributes.

Examples
--------
Basic usage:

>>> @retry
... def fetch_data():
...     # Will retry once if any exception occurs
...     return external_api.fetch()

With configuration:

>>> @retry(limit=3, delay=2.0, catching=ConnectionError)
... async def connect():
...     # Will retry up to 3 times with 2 second delays on ConnectionError
...     return await establish_connection()

With exponential backoff:

>>> def backoff(attempt, exception):
...     return 0.5 * (2 ** attempt)  # 1s, 2s, 4s, ...
...
>>> @retry(limit=5, delay=backoff)
... def unreliable_operation():
...     return perform_operation()

# haiway.helpers.files

## FileException Objects

```python
class FileException(Exception)
```

Exception raised for file operation errors.

Raised when file operations fail, such as attempting to access
a non-existent file without the create flag, or when file I/O
operations encounter errors.

## FileReading Objects

```python
@runtime_checkable
class FileReading(Protocol)
```

Protocol for asynchronous file reading operations.

Implementations read the entire file contents and return them as bytes.
The file position is managed internally and reading always returns the
complete file contents from the beginning.

## FileWriting Objects

```python
@runtime_checkable
class FileWriting(Protocol)
```

Protocol for asynchronous file writing operations.

Implementations write the provided content to the file, completely
replacing any existing content. The write operation is atomic and
includes proper synchronization to ensure data is written to disk.

## File Objects

```python
class File(State)
```

State container for file operations within a context scope.

Provides access to file operations after a file has been opened using
FileAccess within a context scope. Follows Haiway's pattern of accessing
functionality through class methods that retrieve state from the current context.

The file operations are provided through the reading and writing protocol
implementations, which are injected when the file is opened.

#### read

```python
@classmethod
async def read(cls) -> bytes
```

Read the complete contents of the file.

Returns
-------
bytes
    The complete file contents as bytes

Raises
------
FileException
    If no file is currently open in the context

#### write

```python
@classmethod
async def write(cls, content: bytes) -> None
```

Write content to the file, replacing existing content.

Parameters
----------
content : bytes
    The bytes content to write to the file

Raises
------
FileException
    If no file is currently open in the context

## FileContext Objects

```python
@runtime_checkable
class FileContext(Protocol)
```

Protocol for file context managers.

Defines the interface for file context managers that handle the opening,
access, and cleanup of file resources. Implementations ensure proper
resource management and make file operations available through the File
state class.

The context manager pattern ensures that file handles are properly closed
and locks are released even if exceptions occur during file operations.

#### \_\_aenter\_\_

```python
async def __aenter__() -> File
```

Enter the file context and return file operations.

Returns
-------
File
    A File state instance configured for the opened file

Raises
------
FileException
    If the file cannot be opened

#### \_\_aexit\_\_

```python
async def __aexit__(exc_type: type[BaseException] | None,
                    exc_val: BaseException | None,
                    exc_tb: TracebackType | None) -> bool | None
```

Exit the file context and clean up resources.

Parameters
----------
exc_type : type[BaseException] | None
    The exception type if an exception occurred
exc_val : BaseException | None
    The exception value if an exception occurred
exc_tb : TracebackType | None
    The exception traceback if an exception occurred

Returns
-------
bool | None
    None to allow exceptions to propagate

## FileAccessing Objects

```python
@runtime_checkable
class FileAccessing(Protocol)
```

Protocol for file access implementations.

Defines the interface for creating file context managers with specific
access patterns. Implementations handle the details of file opening,
locking, and resource management.

## FileAccess Objects

```python
class FileAccess(State)
```

State container for file access configuration within a context scope.

Provides the entry point for file operations within Haiway's context system.
Follows the framework's pattern of using state classes to configure behavior
that can be injected and replaced for testing.

File access is scoped to the context, meaning only one file can be open
at a time within a given context scope. This design ensures predictable
resource usage and simplifies error handling.

The default implementation provides standard filesystem access with
optional file creation and exclusive locking. Custom implementations
can be injected by replacing the accessing function.

Examples
--------
Basic file operations:

>>> async with ctx.scope("app", disposables=(FileAccess.open("config.json", create=True),)):
...     data = await File.read()
...     await File.write(b'{"setting": "value"}')

Exclusive file access for critical operations:

>>> async with ctx.scope("app", disposables=(FileAccess.open("config.json", exclusive=True),)):
...     content = await File.read()
...     processed = process_data(content)
...     await File.write(processed)

#### open

```python
@classmethod
async def open(cls,
               path: Path | str,
               create: bool = False,
               exclusive: bool = False) -> FileContext
```

Open a file for reading and writing.

Opens a file using the configured file access implementation. The file
is opened with read and write permissions, and the entire file content
is available through the File.read() and File.write() methods.

Parameters
----------
path : Path | str
    The file path to open, as a Path object or string
create : bool, optional
    If True, create the file and parent directories if they don't exist.
    If False, raise FileException for missing files. Default is False
exclusive : bool, optional
    If True, acquire an exclusive lock on the file for the duration of
    the context. This prevents other processes from accessing the file
    concurrently. Default is False

Returns
-------
FileContext
    A FileContext that manages the file lifecycle and provides access
    to file operations through the File state class

Raises
------
FileException
    If the file cannot be opened with the specified parameters, or if
    a file is already open in the current context scope

# haiway.helpers.tracing

#### traced

```python
def traced(
    function: Callable[Args, Result] | None = None,
    *,
    level: ObservabilityLevel = ObservabilityLevel.DEBUG,
    label: str | None = None
) -> Callable[[Callable[Args, Result]],
              Callable[Args, Result]] | Callable[Args, Result]
```

Decorator that adds tracing to functions, recording inputs, outputs, and exceptions.

Automatically records function arguments, return values, and any exceptions
within the current observability context. The recorded data can be used for
debugging, performance analysis, and understanding program execution flow.

In non-debug builds (when __debug__ is False), this decorator has no effect
and returns the original function to avoid performance impact in production.

Parameters
----------
function: Callable[Args, Result] | None
    The function to be traced
level: ObservabilityLevel
    The observability level at which to record trace information (default: DEBUG)
label: str | None
    Custom label for the trace; defaults to the function name if not provided

Returns
-------
Callable
    A decorated function that performs the same operation as the original
    but with added tracing

Notes
-----
Works with both synchronous and asynchronous functions. For asynchronous
functions, properly awaits the result before recording it.

# haiway.helpers.concurrent

#### process\_concurrently

```python
async def process_concurrently(source: AsyncIterator[Element],
                               handler: Callable[[Element], Coroutine[Any, Any,
                                                                      None]],
                               *,
                               concurrent_tasks: int = 2,
                               ignore_exceptions: bool = False) -> None
```

Process elements from an async iterator concurrently.

Parameters
----------
source: AsyncIterator[Element]
    An async iterator providing elements to process.

handler: Callable[[Element], Coroutine[Any, Any, None]]
    A coroutine function that processes each element.

concurrent_tasks: int
    Maximum number of concurrent tasks (must be > 0), default is 2.

ignore_exceptions: bool
    If True, exceptions from tasks will be logged but not propagated,
     default is False.

# haiway.helpers.throttling

#### throttle

```python
def throttle(
    function: Callable[Args, Coroutine[Any, Any, Result]] | None = None,
    *,
    limit: int = 1,
    period: timedelta | float = 1
) -> (Callable[
    [Callable[Args, Coroutine[Any, Any, Result]]],
        Callable[Args, Coroutine[Any, Any, Result]],
]
      | Callable[Args, Coroutine[Any, Any, Result]])
```

Rate-limit asynchronous function calls.

This decorator restricts the frequency of function calls by enforcing a maximum
number of executions within a specified time period. When the limit is reached,
subsequent calls will wait until they can be executed without exceeding the limit.

Can be used as a simple decorator (@throttle) or with configuration
parameters (@throttle(limit=5, period=60)).

Parameters
----------
function: Callable[Args, Coroutine[Any, Any, Result]] | None
    The async function to throttle. When used as a simple decorator,
    this parameter is provided automatically.
limit: int
    Maximum number of executions allowed within the specified period.
    Default is 1, meaning only one call is allowed per period.
period: timedelta | float
    Time window in which the limit applies. Can be specified as a timedelta
    object or as a float (seconds). Default is 1 second.

Returns
-------
Callable
    When used as @throttle: Returns the wrapped function that enforces the rate limit.
    When used as @throttle(...): Returns a decorator that can be applied to a function.

Notes
-----
- Works only with asynchronous functions.
- Cannot be used on class or instance methods.
- Not thread-safe, should only be used within a single event loop.
- The function preserves the original function's signature, docstring, and other attributes.

Examples
--------
Basic usage to limit to 1 call per second:

>>> @throttle
... async def api_call(data):
...     return await external_api.send(data)

Limit to 5 calls per minute:

>>> @throttle(limit=5, period=60)
... async def api_call(data):
...     return await external_api.send(data)

# haiway.helpers

# haiway.helpers.observability

## ScopeStore Objects

```python
class ScopeStore()
```

Internal class for storing scope information during observability tracking.

Tracks timing information, nested scopes, and recorded events for a specific scope.
Used by LoggerObservability to maintain the hierarchy of scopes and their data.

#### time

```python
@property
def time() -> float
```

Calculate the elapsed time in seconds since this scope was entered.

#### exited

```python
@property
def exited() -> bool
```

Check if this scope has been exited.

#### exit

```python
def exit() -> None
```

Mark this scope as exited and record the exit time.

#### completed

```python
@property
def completed() -> bool
```

Check if this scope and all its nested scopes are completed.

A scope is considered completed when it has been exited and all its
nested scopes have also been completed.

#### try\_complete

```python
def try_complete() -> bool
```

Try to mark this scope as completed.

A scope can only be completed if:
- It has been exited
- It has not already been completed
- All its nested scopes are completed

Returns
-------
bool
    True if the scope was successfully marked as completed,
    False if any completion condition was not met

#### LoggerObservability

```python
def LoggerObservability(logger: Logger | None = None,
                        *,
                        debug_context: bool = __debug__) -> Observability
```

Create an Observability implementation that uses a standard Python logger.

This factory function creates an Observability instance that uses a Logger for recording
various types of observability data including logs, events, metrics, and attributes.
It maintains a hierarchical scope structure that tracks timing information and provides
a summary of all recorded data when the root scope exits.

Parameters
----------
logger: Logger | None
    The logger to use for recording observability data. If None, a logger will be
    created based on the scope label when the first scope is entered.
debug_context: bool
    Whether to store and display a detailed hierarchical summary when the root scope
    exits. Defaults to True in debug mode (__debug__) and False otherwise.

Returns
-------
Observability
    An Observability instance that uses the specified logger (or a default one)
    for recording observability data.

Notes
-----
The created Observability instance tracks timing for each scope and records it
when the scope exits. When the root scope exits and debug_context is True,
it produces a hierarchical summary of all recorded events, metrics, and attributes.

# haiway.helpers.caching

## CacheMakeKey Objects

```python
class CacheMakeKey()
```

Protocol for generating cache keys from function arguments.

Implementations of this protocol are responsible for creating a unique key
based on the arguments passed to a function, which can then be used for
cache lookups.

The key must be consistent for the same set of arguments, and different
for different sets of arguments that should be cached separately.

## CacheRead Objects

```python
class CacheRead()
```

Protocol for reading values from a cache.

Implementations of this protocol are responsible for retrieving cached values
based on a key. If the key is not present in the cache, None should be returned.

This is designed as an asynchronous operation to support remote caches where
retrieval might involve network operations.

## CacheWrite Objects

```python
class CacheWrite()
```

Protocol for writing values to a cache.

Implementations of this protocol are responsible for storing values in a cache
using the specified key. Any existing value with the same key should be overwritten.

This is designed as an asynchronous operation to support remote caches where
writing might involve network operations.

#### cache

```python
def cache(
    function: Callable[Args, Result] | None = None,
    *,
    limit: int | None = None,
    expiration: float | None = None,
    make_key: CacheMakeKey[Args, Key] | None = None,
    read: CacheRead[Key, Result] | None = None,
    write: CacheWrite[Key, Result] | None = None
) -> (Callable[
    [Callable[Args, Coroutine[Any, Any, Result]]],
        Callable[Args, Coroutine[Any, Any, Result]],
]
      | Callable[[Callable[Args, Result]], Callable[Args, Result]]
      | Callable[Args, Result])
```

Memoize the result of a function using a configurable cache.

Parameters
----------
function : Callable[Args, Result] | None
    The function to be memoized.
    When used as a simple decorator (i.e., `@cache`), this is the decorated function.
    Should be omitted when cache is called with configuration arguments.
limit : int | None
    The maximum number of entries to keep in the cache.
    Defaults to 1 if not specified.
    Ignored when using custom cache implementations (read/write).
expiration : float | None
    Time in seconds after which a cache entry expires and will be recomputed.
    Defaults to None, meaning entries don't expire based on time.
    Ignored when using custom cache implementations (read/write).
make_key : CacheMakeKey[Args, Key] | None
    Function to generate a cache key from function arguments.
    If None, uses a default implementation that handles most cases.
    Required when using custom cache implementations (read/write).
read : CacheRead[Key, Result] | None
    Custom asynchronous function to read values from cache.
    Must be provided together with `write` and `make_key`.
    Only available for async functions.
write : CacheWrite[Key, Result] | None
    Custom asynchronous function to write values to cache.
    Must be provided together with `read` and `make_key`.
    Only available for async functions.

Returns
-------
Callable
    If `function` is provided as a positional argument, returns the memoized function.
    Otherwise returns a decorator that can be applied to a function to memoize it
    with the given configuration.

Notes
-----
This decorator supports both synchronous and asynchronous functions.
The default implementation uses a simple in-memory LRU cache.
For asynchronous functions, you can provide custom cache implementations
via the `read` and `write` parameters.

The default cache is not thread-safe and should not be used in multi-threaded
applications without external synchronization.

Examples
--------
Simple usage as a decorator:

>>> @cache
... def my_function(x: int) -> int:
...     print("Function called")
...     return x * 2
>>> my_function(5)
Function called
10
>>> my_function(5)  # Cache hit, function body not executed
10

With configuration parameters:

>>> @cache(limit=10, expiration=60.0)
... def my_function(x: int) -> int:
...     return x * 2

With custom cache for async functions:

>>> @cache(make_key=custom_key_maker, read=redis_read, write=redis_write)
... async def fetch_data(user_id: str) -> dict:
...     return await api_call(user_id)

# haiway.helpers.asynchrony

#### asynchronous

```python
def asynchronous(
    function: Callable[Args, Result] | None = None,
    *,
    loop: AbstractEventLoop | None = None,
    executor: Executor | Missing = MISSING
) -> (Callable[
    [Callable[Args, Result]],
        Callable[Args, Coroutine[Any, Any, Result]],
]
      | Callable[Args, Coroutine[Any, Any, Result]])
```

Convert a synchronous function to an asynchronous one that runs in an executor.

This decorator transforms synchronous, potentially blocking functions into
asynchronous coroutines that execute in an event loop's executor, allowing
them to be used with async/await syntax without blocking the event loop.

Can be used as a simple decorator (@asynchronous) or with configuration
parameters (@asynchronous(executor=my_executor)).

Parameters
----------
function: Callable[Args, Result] | None
    The synchronous function to be wrapped. When used as a simple decorator,
    this parameter is provided automatically.
loop: AbstractEventLoop | None
    The event loop to run the function in. When None is provided, the currently
    running loop while executing the function will be used. Default is None.
executor: Executor | Missing
    The executor used to run the function. When not provided, the default loop
    executor will be used. Useful for CPU-bound tasks or operations that would
    otherwise block the event loop.

Returns
-------
Callable
    When used as @asynchronous: Returns the wrapped function that can be awaited.
    When used as @asynchronous(...): Returns a decorator that can be applied to a function.

Notes
-----
The function preserves the original function's signature, docstring, and other attributes.
Context variables from the calling context are preserved when executing in the executor.

Examples
--------
Basic usage:

>>> @asynchronous
... def cpu_intensive_task(data):
...     # This runs in the default executor
...     return process_data(data)
...
>>> await cpu_intensive_task(my_data)  # Non-blocking

With custom executor:

>>> @asynchronous(executor=process_pool)
... def cpu_intensive_task(data):
...     return process_data(data)

# haiway.helpers.timeouting

#### timeout

```python
def timeout(
    timeout: float
) -> Callable[
    [Callable[Args, Coroutine[Any, Any, Result]]],
        Callable[Args, Coroutine[Any, Any, Result]],
]
```

Add a timeout to an asynchronous function.

This decorator enforces a maximum execution time for the decorated function.
If the function does not complete within the specified timeout period, it
will be cancelled and a TimeoutError will be raised.

Parameters
----------
timeout: float
    Maximum execution time in seconds allowed for the function

Returns
-------
Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Callable[Args, Coroutine[Any, Any, Result]]]
    A decorator that can be applied to an async function to add timeout behavior

Notes
-----
- Works only with asynchronous functions.
- The wrapped function will be properly cancelled when the timeout occurs.
- Not thread-safe, should only be used within a single event loop.
- The original function should handle cancellation properly to ensure
  resources are released when timeout occurs.

Examples
--------
>>> @timeout(5.0)
... async def fetch_data(url):
...     # Will raise TimeoutError if it takes more than 5 seconds
...     return await http_client.get(url)

