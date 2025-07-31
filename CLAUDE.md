# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Setup

- `make venv` - Setup development environment and install git hooks
- `source .venv/bin/activate && make sync` - Sync dependencies with uv lock file
- `source .venv/bin/activate && make update` - Update and lock dependencies

### Code Quality

- `source .venv/bin/activate && make format` - Format code with Ruff
- `source .venv/bin/activate && make lint` - Run linters (Ruff + Bandit + Pyright strict mode)
- `source .venv/bin/activate && make test` - Run pytest with coverage
- `source .venv/bin/activate && pytest tests/test_specific.py` - Run single test file
- `source .venv/bin/activate && pytest tests/test_specific.py::test_function` - Run specific test

## Framework Architecture

Haiway is a functional programming framework for Python 3.12+ emphasizing immutability and structured concurrency. The framework is built around three core pillars:

### 1. Context System (`haiway/context/`)

**Central execution environment with lifecycle management:**
- `ScopeContext` - Main context manager with 4-layer state priority (explicit > disposables > presets > contextual)
- `StateContext` - Type-based state resolution and propagation
- `TaskGroupContext` - Structured concurrency with automatic cleanup
- `EventsContext` - Type-safe event bus for scope-local communication
- `VariablesContext` - Mutable scope-local variables with propagation
- `ObservabilityContext` - Integrated logging, metrics, and tracing

**Key File**: `src/haiway/context/access.py` - Contains the main `ctx` API

### 2. State System (`haiway/state/`)

**Immutable data structures with validation:**
- `StateMeta` - Metaclass handling attribute processing and generic specialization
- `State` - Base class providing immutability, validation, and path-based updates
- `AttributeValidator` - Runtime type validation for all Python types including generics
- `AttributePath` - Type-safe path-based updates using `Class._.attribute` syntax

**Key Files**:
- `src/haiway/state/structure.py` - Main State class implementation
- `src/haiway/state/validation.py` - Type validation system
- `src/haiway/state/path.py` - Path-based update mechanism

### 3. Helpers System (`haiway/helpers/`)

**Async utilities and integrations:**
- HTTP client with pluggable backends (HTTPX included)
- Concurrency utilities (retry, timeout, throttle, cache)
- File access abstraction
- Observability integration

## Framework Development Patterns

### Extending Core Components

**Adding New Context Features:**
1. Create context class in `haiway/context/`
2. Integrate with `ScopeContext.__aenter__()` and `__aexit__()`
3. Add API methods to `ctx` class in `access.py`
4. Export in `haiway/context/__init__.py`

**Adding State Validation Types:**
1. Extend `AttributeValidator` in `haiway/state/validation.py`
2. Add type patterns to validation registry
3. Handle generic type parameters if needed

**Adding Helper Utilities:**
1. Create module in `haiway/helpers/`
2. Follow protocol-based design for pluggable implementations
3. Use `State` classes for configuration
4. Export in `haiway/helpers/__init__.py`

### Framework Architecture Principles

**State Priority System:**
```python
# Context resolution order (highest to lowest priority):
async with ctx.scope(
    "example",
    ExplicitState(),           # 1. Explicit state (highest)
    disposables=[resource],    # 2. Disposable resources
    preset=preset,            # 3. Context presets
    # 4. Parent context state (lowest)
):
```

**Protocol-Based Extensions:**
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class CustomProtocol(Protocol):
    async def __call__(self, param: str) -> Result: ...

class CustomHelper(State):
    implementation: CustomProtocol
    
    @classmethod 
    async def method(cls, param: str) -> Result:
        return await ctx.state(cls).implementation(param)
```

**Disposable Resource Pattern:**
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def custom_resource():
    resource = await setup_resource()
    try:
        yield CustomState(resource=resource.interface)
    finally:
        await resource.cleanup()
```

## ⚠️  CRITICAL: STRICT TYPING REQUIREMENTS ⚠️

**Haiway framework development REQUIRES complete type annotations - this is NON-NEGOTIABLE:**

- **Every State attribute** MUST have explicit type annotations
- **Every function/method** MUST have typed parameters and return types
- **Every Protocol method** MUST have complete type signatures
- **ONLY abstract collection types** - `Sequence`, `Mapping`, `Set` (never `list`, `dict`, `set`)
- **Modern union syntax ONLY** - `str | None` (never `Optional[str]`)
- **Runtime type validation** - missing or incorrect types cause immediate failures
- **Generic types must be explicit** - avoid `Any` unless absolutely necessary

## Framework Development Guidelines

### Code Style and Standards

- **Imports**: Use absolute imports from `haiway` package, export symbols in `__init__.py`
- **Type System**: MANDATORY complete type annotations - Python 3.12+ features (unions with `|`, new generic syntax)
- **Collections**: ALWAYS use abstract types (`Sequence`, `Mapping`, `Set`) in State classes - NEVER concrete types
- **Error Handling**: Create custom exceptions extending base framework exceptions
- **Naming**: Use continuous tense for protocol names (`UserFetching`, `DataProcessing`)
- **STRICT TYPING REQUIREMENT**: Every variable, parameter, return type, and State attribute MUST have type annotations

### Internal Architecture Patterns

**Metaclass Usage:**
- `StateMeta` handles attribute processing and generic specialization
- Use `dataclass_transform` decorator for IDE/type checker compatibility
- Cache parameterized types in `_types_cache` WeakValueDictionary

**Context Integration:**
- Context components use `contextvars` for thread-safe state propagation
- All context managers follow enter/exit lifecycle with proper cleanup
- State resolution uses type-based lookup with priority system

**Validation System:**
- `AttributeValidator` registry handles all Python type patterns
- Immutable collections automatically converted (list→tuple, set→frozenset)
- Generic type parameters validated at runtime

### Testing Framework Components

- Mock context dependencies using `ctx.scope()` with test state
- Test async functionality with `@pytest.mark.asyncio`
- Use protocol implementations for isolated unit testing

## Usage Examples

### State System Patterns

```python
from typing import Sequence, Mapping, Set, Protocol, runtime_checkable
from haiway import State, ctx

# Immutable collections (MANDATORY: ONLY abstract types, COMPLETE type annotations)
class UserData(State):
    id: str                          # REQUIRED: type annotation
    name: str                        # REQUIRED: all attributes must be typed
    roles: Sequence[str] = ()        # NEVER list[str] → becomes tuple
    metadata: Mapping[str, Any] = {} # NEVER dict[str, Any] → stays dict but validated
    tags: Set[str] = frozenset()     # NEVER set[str] → becomes frozenset

# Generic state classes - FULL TYPE ANNOTATIONS REQUIRED
class Container[Element](State):
    items: Sequence[Element]         # REQUIRED: type annotation with generic parameter
    created_at: datetime             # REQUIRED: all attributes must be typed

# Protocol-based dependency injection - COMPLETE METHOD SIGNATURES REQUIRED
@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, id: str) -> UserData: ...  # REQUIRED: full signature typing

class UserService(State):
    fetching: UserFetching           # REQUIRED: protocol type annotation
    
    @classmethod
    async def get_user(cls, id: str) -> UserData:  # REQUIRED: full method typing
        return await ctx.state(cls).fetching(id)

# State updates and path-based modifications
user = UserData(id="1", name="Alice")
updated = user.updated(name="Alice Smith")
nested_update = user.updating(UserData._.metadata, {"role": "admin"})
```

### Context and Resource Management

```python
from contextlib import asynccontextmanager

# Disposable resource pattern
@asynccontextmanager
async def database_connection():
    conn = await create_connection()
    try:
        yield DatabaseService(connection=conn)
    finally:
        await conn.close()

# Context usage with state priority
async def main():
    service_impl = UserService(fetching=mock_fetcher)
    
    async with ctx.scope(
        "app",
        service_impl,                    # Explicit state (highest priority)
        disposables=[database_connection()],  # Resource state
        preset=dev_preset,               # Preset state
        # Parent context state (lowest priority)
    ):
        user = await UserService.get_user("123")
        
        # Nested context with variable tracking
        ctx.variable(Counter(value=0))
        async with ctx.scope("operation"):
            counter = ctx.variable(Counter, default=Counter())
            ctx.variable(counter.updated(value=counter.value + 1))
```

### Complete ctx API Reference

```python
# State access and management
user_data = ctx.state(UserData)                    # Get state by type
has_config = ctx.check_state(AppConfig)            # Check if state exists
updated_ctx = ctx.updated(new_state)               # Create nested context with updated state

# Context variables (mutable, scope-local)
ctx.variable(Counter(value=0))                     # Set variable
counter = ctx.variable(Counter)                    # Get variable (may be None)
counter = ctx.variable(Counter, default=Counter()) # Get with default

# Task management and concurrency  
task = ctx.spawn(async_function, arg1, arg2)       # Spawn task in current scope's task group
ctx.check_cancellation()                          # Check if current task is cancelled
ctx.cancel()                                       # Cancel current task

# Event system (only in root scopes)
ctx.send(UserCreated(user_id=user.id))            # Send event
async for event in ctx.subscribe(UserCreated):    # Subscribe to events
    process_event(event)

# Logging and observability
ctx.log_info("Operation completed", extra_data=value)
ctx.log_warning("Potential issue", exception=exc)
ctx.log_error("Error occurred", exception=exc)
ctx.log_debug("Debug info", details=data)

# Observability recording
ctx.record(attributes={"key": "value"})                    # Record attributes
ctx.record(event="user_login", attributes={"user": "123"}) # Record event
ctx.record(metric="response_time", value=0.5, kind=ObservabilityMetricKind.GAUGE)

# Trace identification
trace_id = ctx.trace_id()                          # Get current trace ID

# Resource management
async with ctx.disposables(resource1, resource2): # Manage multiple disposables
    # Resources available as state

# Context streaming
async for result in ctx.stream(async_generator_func, args): # Stream results with context
    process_result(result)
```

### State Type Validation System

```python
# All supported types validated at runtime - COMPLETE TYPE ANNOTATIONS REQUIRED:
class CompleteStateExample(State):
    # Basic types - EVERY ATTRIBUTE MUST HAVE TYPE ANNOTATION
    text: str = "default"
    number: int = 0
    flag: bool = True
    decimal: float = 0.0
    data: bytes = b""
    
    # Optional and union types - USE | SYNTAX ONLY, NEVER Optional[T]
    optional_text: str | None = None
    number_or_text: int | str = 0
    
    # Collections (CRITICAL: ONLY abstract types, NEVER list/dict/set)
    items: Sequence[str] = ()           # NEVER list[str] → becomes tuple (immutable)
    mapping: Mapping[str, int] = {}     # NEVER dict[str, int] → stays dict (validated)
    unique_items: Set[str] = frozenset() # NEVER set[str] → becomes frozenset (immutable)
    
    # Nested state and generics
    nested: UserData | None = None
    container: Container[UserData] | None = None
    
    # Special types
    identifier: UUID = uuid4()
    timestamp: datetime = datetime.now()
    path: Path = Path(".")
    pattern: re.Pattern[str] = re.compile(r".*")
    
    # Enums and literals
    status: Literal["active", "inactive"] = "active"
    priority: Priority = Priority.NORMAL  # Enum type
    
    # Callable protocols
    processor: DataProcessing | None = None
    
    # Any type (no validation)
    raw_data: Any = None

# Generic state with type parameter validation
class TypedContainer[T](State):
    items: Sequence[T]
    metadata: Mapping[str, Any] = {}

# Usage with validation
StringContainer = TypedContainer[str]  # Specialization cached
container = StringContainer(items=["a", "b", "c"])  # ✓ Valid
# container = StringContainer(items=[1, 2, 3])      # ✗ TypeError
```

### Framework Extension Examples

```python
# Adding new helper utility
from haiway.helpers import HTTPRequesting, HTTPResponse

class CustomHTTPClient(State):
    endpoint: str
    api_key: str
    
    @classmethod
    async def request(cls, path: str) -> HTTPResponse:
        client_state = ctx.state(cls)
        ctx.log_info(f"Making request to {client_state.endpoint}{path}")
        # Implementation using client_state.endpoint, client_state.api_key
        
# Testing with mocks
@pytest.mark.asyncio
async def test_user_service():
    async def mock_fetcher(id: str) -> UserData:
        return UserData(id=id, name="Test User")
    
    mock_service = UserService(fetching=mock_fetcher)
    async with ctx.scope("test", mock_service):
        user = await UserService.get_user("123")
        assert user.name == "Test User"
        
        # Test context state access
        assert ctx.check_state(UserService)
        service = ctx.state(UserService)
        assert service.fetching == mock_fetcher
```
