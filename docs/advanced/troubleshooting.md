# Troubleshooting

This guide helps you diagnose and resolve common issues when working with Haiway.

## Common Issues

### Context State Access Issues

#### Problem: `StateNotFoundError`

```python
# ❌ This will raise StateNotFoundError
class UserService(State):
    name: str = "user_service"

async def main():
    async with ctx.scope("app"):
        # UserService was not added to context
        service = ctx.state(UserService)  # Raises StateNotFoundError
```

**Solution**: Ensure state is added to context scope

```python
# ✅ Correct approach
async def main():
    user_service = UserService(name="user_service")
    async with ctx.scope("app", user_service):
        service = ctx.state(UserService)  # Works correctly
```

#### Problem: Context Not Available

```python
# ❌ This will raise ContextNotFoundError
async def function_without_context():
    ctx.log_info("This will fail")  # No active context

async def main():
    await function_without_context()  # Raises ContextNotFoundError
```

**Solution**: Ensure functions are called within a context scope

```python
# ✅ Correct approach
async def function_with_context():
    ctx.log_info("This works")  # Context is available

async def main():
    async with ctx.scope("app"):
        await function_with_context()  # Works correctly
```

### State Management Issues

#### Problem: Attempting to Modify Immutable State

```python
# ❌ This will raise AttributeError
class UserData(State):
    name: str
    age: int

user = UserData(name="Alice", age=30)
user.name = "Bob"  # Raises AttributeError
```

**Solution**: Use the `updated()` method

```python
# ✅ Correct approach
user = UserData(name="Alice", age=30)
updated_user = user.updated(name="Bob")
```

#### Problem: Using Mutable Collections in State

```python
# ❌ This creates mutable state
class BadState(State):
    items: list[str] = []  # Mutable list
    metadata: dict[str, str] = {}  # Mutable dict

# This allows unwanted mutations
state = BadState()
state.items.append("new_item")  # Mutates the state!
```

**Solution**: Use immutable collection types

```python
# ✅ Correct approach
from typing import Sequence, Mapping

class GoodState(State):
    items: Sequence[str] = ()  # Becomes tuple
    metadata: Mapping[str, str] = {}  # Becomes immutable
```

### Async/Await Issues

#### Problem: Forgetting `await` with Async Functions

```python
# ❌ Common mistake
async def async_operation():
    await asyncio.sleep(1)
    return "result"

async def main():
    result = async_operation()  # Missing await!
    print(result)  # Prints coroutine object, not "result"
```

**Solution**: Always `await` coroutines

```python
# ✅ Correct approach
async def main():
    result = await async_operation()  # Properly awaited
    print(result)  # Prints "result"
```

#### Problem: Blocking Operations in Async Functions

```python
# ❌ This blocks the event loop
import time

async def bad_async_function():
    time.sleep(1)  # Blocking call!
    return "done"
```

**Solution**: Use async alternatives

```python
# ✅ Non-blocking approach
import asyncio

async def good_async_function():
    await asyncio.sleep(1)  # Non-blocking
    return "done"
```

### Resource Management Issues

#### Problem: Resource Leaks

```python
# ❌ Resource might not be cleaned up on error
async def risky_resource_usage():
    connection = await create_connection()
    
    # If this raises an error, connection is never closed
    result = await risky_operation()
    
    await connection.close()  # Might never be reached
    return result
```

**Solution**: Use proper resource management

```python
# ✅ Resources always cleaned up
@asynccontextmanager
async def managed_connection():
    connection = await create_connection()
    try:
        yield connection
    finally:
        await connection.close()

async def safe_resource_usage():
    async with managed_connection() as connection:
        return await risky_operation(connection)
    # Connection automatically closed
```

### Performance Issues

#### Problem: Too Much Concurrency

```python
# ❌ This can overwhelm the system
async def process_all_items(items):
    tasks = [process_item(item) for item in items]  # All at once!
    return await asyncio.gather(*tasks)
```

**Solution**: Limit concurrency

```python
# ✅ Controlled concurrency
from haiway.helpers import process_concurrently

async def process_all_items(items):
    return await process_concurrently(
        process_item,
        items,
        max_concurrency=10  # Reasonable limit
    )
```

#### Problem: Memory Leaks from Large State Objects

```python
# ❌ Accumulating large state objects
class DataProcessor(State):
    all_processed_data: Sequence[dict] = ()  # Grows indefinitely

async def process_stream():
    processor = DataProcessor()
    
    async for data in data_stream():
        processed = await process_data(data)
        # This keeps growing in memory!
        processor = processor.updated(
            all_processed_data=processor.all_processed_data + (processed,)
        )
```

**Solution**: Avoid accumulating data in state

```python
# ✅ Process without accumulating
async def process_stream():
    async for data in data_stream():
        processed = await process_data(data)
        await save_processed_data(processed)  # Save immediately
        # Don't keep in memory
```

## Debugging Techniques

### Enable Debug Logging

```python
import logging
from haiway import ctx

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

async def main():
    logger = logging.getLogger("debug")
    
    async with ctx.scope("app", logger=logger):
        ctx.log_debug("Debug information")
        ctx.log_info("Application started")
```

### Context Introspection

```python
async def debug_context():
    """Debug context state"""
    current = ctx.current()
    
    print(f"Context name: {current.name}")
    print(f"Context ID: {current.id}")
    
    # List all state objects in context
    for state_type in current.states:
        print(f"Available state: {state_type.__name__}")
```

### State Inspection

```python
from haiway import State

def debug_state(state: State):
    """Debug state object"""
    print(f"State type: {type(state).__name__}")
    print(f"State fields: {state.__dataclass_fields__.keys()}")
    
    for field_name, field_value in state.__dict__.items():
        print(f"  {field_name}: {type(field_value).__name__} = {field_value}")
```

### Performance Profiling

```python
import cProfile
import pstats
from haiway import ctx

async def profile_operation():
    """Profile a specific operation"""
    
    profiler = cProfile.Profile()
    profiler.enable()
    
    try:
        # Your operation here
        await expensive_operation()
    finally:
        profiler.disable()
        
        # Print stats
        stats = pstats.Stats(profiler)
        stats.sort_stats('cumulative')
        stats.print_stats(10)  # Top 10 functions
```

### Memory Debugging

```python
import tracemalloc
from haiway import ctx

async def debug_memory():
    """Debug memory usage"""
    
    # Start memory tracing
    tracemalloc.start()
    
    # Take initial snapshot
    snapshot1 = tracemalloc.take_snapshot()
    
    # Your operation here
    await memory_intensive_operation()
    
    # Take final snapshot
    snapshot2 = tracemalloc.take_snapshot()
    
    # Compare snapshots
    top_stats = snapshot2.compare_to(snapshot1, 'lineno')
    
    print("Top 10 memory allocations:")
    for index, stat in enumerate(top_stats[:10], 1):
        print(f"{index}. {stat}")
```

## Common Error Messages

### `StateNotFoundError`

**Error**: `StateNotFoundError: State type 'UserService' not found in context`

**Cause**: Attempting to access state that wasn't added to the current context.

**Solutions**:
1. Add the state to the context scope
2. Check if you're in the correct context
3. Verify the state type name

### `ContextNotFoundError`

**Error**: `ContextNotFoundError: No active context found`

**Cause**: Calling context functions outside of a context scope.

**Solutions**:
1. Wrap calls in `async with ctx.scope(...)`
2. Ensure you're in an async function
3. Check context nesting

### `ValidationError`

**Error**: `ValidationError: Invalid value for field 'age': -5`

**Cause**: State validation failed in `__post_init__`.

**Solutions**:
1. Check input data validity
2. Review validation logic
3. Handle validation errors appropriately

### `TimeoutError`

**Error**: `asyncio.TimeoutError`

**Cause**: Operation exceeded timeout limit.

**Solutions**:
1. Increase timeout if appropriate
2. Optimize the operation
3. Add progress reporting for long operations

## Testing Issues

### Problem: Tests Hang Indefinitely

```python
# ❌ Test doesn't complete
async def test_hanging():
    async with ctx.scope("test"):
        # Forgot to await
        result = long_running_operation()  # Missing await
        assert result == "expected"
```

**Solution**: Ensure all async operations are awaited

```python
# ✅ Test completes properly
async def test_working():
    async with ctx.scope("test"):
        result = await long_running_operation()  # Properly awaited
        assert result == "expected"
```

### Problem: Tests Interfere with Each Other

```python
# ❌ Tests share state
shared_state = UserService()

async def test_user_creation():
    async with ctx.scope("test", shared_state):
        # Modifies shared_state
        await UserService.create_user("Alice")

async def test_user_count():
    async with ctx.scope("test", shared_state):
        # Sees state from previous test!
        count = await UserService.count_users()
        assert count == 0  # Might fail
```

**Solution**: Create fresh state for each test

```python
# ✅ Tests are isolated
async def test_user_creation():
    fresh_state = UserService()  # Fresh state
    async with ctx.scope("test", fresh_state):
        await UserService.create_user("Alice")

async def test_user_count():
    fresh_state = UserService()  # Fresh state
    async with ctx.scope("test", fresh_state):
        count = await UserService.count_users()
        assert count == 0  # Always passes
```

## Development Tools

### VS Code Configuration

Add to `.vscode/settings.json`:

```json
{
    "python.analysis.typeCheckingMode": "strict",
    "python.linting.enabled": true,
    "python.linting.pylintEnabled": false,
    "python.linting.flake8Enabled": true,
    "python.formatting.provider": "black",
    "python.testing.pytestEnabled": true,
    "python.testing.unittestEnabled": false
}
```

### Pytest Configuration

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
python_functions = ["test_*"]
asyncio_mode = "auto"
addopts = [
    "-v",
    "--strict-markers",
    "--disable-warnings",
    "--cov=haiway",
    "--cov-report=term-missing",
]
```

### Pre-commit Hooks

Add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.3.0
    hooks:
      - id: black
        language_version: python3.12

  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort
        args: ["--profile", "black"]

  - repo: https://github.com/charliermarsh/ruff-pre-commit
    rev: v0.0.261
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
```

## Getting Help

### Community Resources

1. **GitHub Issues**: [Report bugs and request features](https://github.com/miquido/haiway/issues)
2. **GitHub Discussions**: [Ask questions and share tips](https://github.com/miquido/haiway/discussions)
3. **Documentation**: [Complete guides and API reference](https://miquido.github.io/haiway/)

### Creating Good Bug Reports

Include the following information:

1. **Haiway version**: `pip show haiway`
2. **Python version**: `python --version`
3. **Operating system**: Windows/macOS/Linux
4. **Minimal reproduction case**:
   ```python
   # Minimal code that reproduces the issue
   from haiway import ctx, State
   
   class TestState(State):
       value: str
   
   async def reproduce_bug():
       async with ctx.scope("test"):
           # Bug occurs here
           pass
   ```
5. **Expected behavior**: What should happen
6. **Actual behavior**: What actually happens
7. **Error messages**: Full stack traces

### Performance Issues

When reporting performance issues, include:

1. **Performance metrics**: Memory usage, CPU usage, response times
2. **Data size**: Amount of data being processed
3. **Concurrency level**: Number of concurrent operations
4. **Profiling results**: Use `cProfile` or similar tools

## Prevention Strategies

### Code Review Checklist

- [ ] All async functions properly awaited
- [ ] State objects use immutable collections
- [ ] Resources properly managed with context managers
- [ ] Appropriate error handling
- [ ] Tests cover error cases
- [ ] Performance considerations addressed

### Monitoring and Alerting

```python
from haiway import ctx
import psutil
import asyncio

async def health_monitor():
    """Monitor application health"""
    
    while True:
        # Check memory usage
        memory_percent = psutil.virtual_memory().percent
        if memory_percent > 80:
            ctx.log_warning(f"High memory usage: {memory_percent}%")
        
        # Check active tasks
        active_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
        if active_tasks > 100:
            ctx.log_warning(f"High task count: {active_tasks}")
        
        await asyncio.sleep(30)  # Check every 30 seconds
```

## Next Steps

- Set up proper logging and monitoring
- Implement health checks
- Create comprehensive test coverage
- Profile your application under load
- Join the Haiway community for support