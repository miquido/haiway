# Context Variables

Context variables provide scoped, mutable state within Haiway's immutable context system. They enable controlled state updates that propagate from child to parent scopes on exit.

## Key Behaviors

1. **No inheritance**: Child scopes start with empty variable sets
2. **Upward propagation**: All child variables overwrite parent variables on scope exit
3. **Task isolation**: Spawned tasks have completely isolated variable contexts
4. **Error resilience**: Variables propagate even when exceptions occur

## Basic Usage

```python
from haiway import ctx, State

class Counter(State):
    value: int = 0

async def example():
    # Set variable (pass instance)
    ctx.variable(Counter(value=1))

    # Get variable (pass type)
    counter = ctx.variable(Counter)
    assert counter.value == 1

    # Get with default
    counter = ctx.variable(Counter, default=Counter(value=0))
```

## Propagation Semantics

```python
async def parent_child_example():
    async with ctx.scope("parent"):
        ctx.variable(Counter(value=10))

        async with ctx.scope("child"):
            # Child doesn't see parent's variable
            assert ctx.variable(Counter) is None

            # Child sets its own
            ctx.variable(Counter(value=20))

        # Parent's variable is now overwritten
        assert ctx.variable(Counter).value == 20
```

## Common Patterns

### Result Propagation

Child scopes compute results that flow to parents:

```python
class Result(State):
    value: float
    error: str | None = None

async def computation():
    async with ctx.scope("main"):
        async with ctx.scope("compute"):
            try:
                result = await expensive_operation()
                ctx.variable(Result(value=result))
            except Exception as e:
                ctx.variable(Result(value=0.0, error=str(e)))

        # Parent receives the result
        result = ctx.variable(Result)
```

### Selective Updates

Update specific variables while preserving others:

```python
async with ctx.scope("parent"):
    ctx.variable(Counter(value=1))
    ctx.variable(Config(debug=False))

    async with ctx.scope("child"):
        # Only update Counter
        ctx.variable(Counter(value=2))

    # Counter updated, Config preserved
    assert ctx.variable(Counter).value == 2
    assert ctx.variable(Config).debug is False
```

## Task Isolation

Spawned tasks cannot affect parent variables:

```python
async with ctx.scope("main"):
    ctx.variable(Counter(value=0))

    async def task():
        # Task has isolated context
        assert ctx.variable(Counter) is None
        ctx.variable(Counter(value=999))  # Won't affect parent

    await ctx.spawn(task)
    assert ctx.variable(Counter).value == 0  # Unchanged
```

## When to Use

**Good for:**

- Temporary computation results
- Override-based configuration
- Error context that survives exceptions
- Scoped state without parameter passing

**Not suitable for:**

- Accumulating values across scopes (use explicit passing)
- Cross-task communication (use events)
- Persistent state (use `ctx.state()`)
- Complex merging logic (implement custom solutions)
