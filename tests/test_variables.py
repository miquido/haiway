import asyncio

from pytest import mark, raises

from haiway import MissingContext, State, ctx
from haiway.context.variables import VariablesContext


class Counter(State):
    value: int = 0


class Metrics(State):
    requests: int = 0
    errors: int = 0
    latency: float = 0.0


class Config(State):
    debug: bool = False
    timeout: int = 30


@mark.asyncio
async def test_basic_set_and_get():
    async with ctx.scope("test"):
        # Initially no variable set
        assert ctx.variable(Counter) is None

        # Set a variable
        ctx.variable(Counter(value=42))

        # Retrieve the variable
        counter = ctx.variable(Counter)
        assert counter is not None
        assert counter.value == 42

        # Update the variable
        ctx.variable(Counter(value=100))
        counter = ctx.variable(Counter)
        assert counter.value == 100


@mark.asyncio
async def test_multiple_variable_types():
    async with ctx.scope("test"):
        # Set different types of variables
        ctx.variable(Counter(value=10))
        ctx.variable(Metrics(requests=100, errors=5))
        ctx.variable(Config(debug=True, timeout=60))

        # Retrieve each type
        counter = ctx.variable(Counter)
        metrics = ctx.variable(Metrics)
        config = ctx.variable(Config)

        assert counter is not None and counter.value == 10
        assert metrics is not None and metrics.requests == 100
        assert config is not None and config.debug is True


@mark.asyncio
async def test_variable_with_default():
    async with ctx.scope("test"):
        # Variable not set, but default provided
        counter = ctx.variable(Counter, default=Counter(value=999))
        assert counter.value == 999

        # Set the variable
        ctx.variable(Counter(value=123))

        # Now it returns the set value, not default
        counter = ctx.variable(Counter, default=Counter(value=999))
        assert counter.value == 123


@mark.asyncio
async def test_no_inheritance_from_parent():
    async with ctx.scope("parent"):
        # Set variable in parent
        ctx.variable(Counter(value=100))
        ctx.variable(Config(debug=True))

        async with ctx.scope("child"):
            # Child doesn't see parent's variables
            assert ctx.variable(Counter) is None
            assert ctx.variable(Config) is None

            # Child sets its own values
            ctx.variable(Counter(value=200))

            # Child sees its own value
            assert ctx.variable(Counter).value == 200
            # Still doesn't see parent's Config
            assert ctx.variable(Config) is None


@mark.asyncio
async def test_propagation_to_parent():
    async with ctx.scope("parent"):
        # Set initial values
        ctx.variable(Counter(value=10))
        ctx.variable(Config(debug=False))

        async with ctx.scope("child"):
            # Child doesn't inherit
            assert ctx.variable(Counter) is None

            # Child sets new values
            ctx.variable(Counter(value=20))
            ctx.variable(Metrics(requests=50))

        # After child exits, parent has child's values
        counter = ctx.variable(Counter)
        metrics = ctx.variable(Metrics)
        config = ctx.variable(Config)

        assert counter.value == 20  # Overwritten by child
        assert metrics.requests == 50  # New from child
        assert config.debug is False  # Unchanged (child didn't set)


@mark.asyncio
async def test_nested_scope_propagation():
    async with ctx.scope("root"):
        ctx.variable(Counter(value=1))

        async with ctx.scope("level1"):
            # Doesn't see parent
            assert ctx.variable(Counter) is None
            ctx.variable(Counter(value=2))

            async with ctx.scope("level2"):
                # Doesn't see parent
                assert ctx.variable(Counter) is None
                ctx.variable(Counter(value=3))

            # level1 now has value from level2
            assert ctx.variable(Counter).value == 3

        # root now has value propagated through levels
        assert ctx.variable(Counter).value == 3


@mark.asyncio
async def test_isolated_task_variables():
    async with ctx.scope("test"):
        ctx.variable(Counter(value=100))
        ctx.variable(Config(debug=True))

        task_executed = asyncio.Event()

        async def task_func():
            # Task doesn't see parent's variables
            assert ctx.variable(Counter) is None
            assert ctx.variable(Config) is None

            # Task sets its own variables
            ctx.variable(Counter(value=999))
            ctx.variable(Metrics(requests=1000))

            task_executed.set()

        # Spawn task
        task = ctx.spawn(task_func)
        await task_executed.wait()
        await task

        # Parent's variables are unchanged
        assert ctx.variable(Counter).value == 100
        assert ctx.variable(Config).debug is True
        # Parent doesn't see task's new variable
        assert ctx.variable(Metrics) is None


@mark.asyncio
async def test_multiple_tasks_isolation():
    async with ctx.scope("test"):
        ctx.variable(Counter(value=0))

        results = []
        tasks_started = asyncio.Event()
        proceed = asyncio.Event()

        async def task_func(task_id: int):
            # Each task starts with no variables
            assert ctx.variable(Counter) is None

            # Set task-specific value
            ctx.variable(Counter(value=task_id * 100))

            if task_id == 2:  # Last task
                tasks_started.set()

            await proceed.wait()

            # Verify task still sees its own value
            counter = ctx.variable(Counter)
            results.append((task_id, counter.value))

        # Spawn multiple tasks
        tasks = [ctx.spawn(task_func, i) for i in range(3)]

        # Wait for all tasks to set their variables
        await tasks_started.wait()

        # Let tasks proceed
        proceed.set()

        # Wait for completion
        for task in tasks:
            await task

        # Verify each task had isolated variables
        results.sort()
        assert results == [(0, 0), (1, 100), (2, 200)]

        # Parent still has original value
        assert ctx.variable(Counter).value == 0


@mark.asyncio
async def test_missing_context_errors():
    # Test get outside any context
    with raises(MissingContext):
        ctx.variable(Counter)

    # Test set outside any context
    with raises(MissingContext):
        ctx.variable(Counter(value=42))

    # Direct VariablesContext usage
    with raises(MissingContext):
        VariablesContext.get(Counter)

    with raises(MissingContext):
        VariablesContext.set(Counter())


@mark.asyncio
async def test_reentrant_context_not_allowed():
    var_ctx = VariablesContext(isolated=False)

    with var_ctx:
        # Try to enter the same context again
        with raises(AssertionError, match="Context reentrance is not allowed"):
            with var_ctx:
                pass


@mark.asyncio
async def test_isolated_context_no_propagation():
    # Root contexts are isolated
    async with ctx.scope("root1"):
        ctx.variable(Counter(value=100))

    async with ctx.scope("root2"):
        # Doesn't see previous root's variables
        assert ctx.variable(Counter) is None


@mark.asyncio
async def test_complex_propagation_scenario():
    async with ctx.scope("root"):
        ctx.variable(Counter(value=1))
        ctx.variable(Config(debug=False))

        async with ctx.scope("branch1"):
            ctx.variable(Counter(value=10))
            ctx.variable(Metrics(requests=100))

        # Root now has branch1's values
        assert ctx.variable(Counter).value == 10
        assert ctx.variable(Metrics).requests == 100
        assert ctx.variable(Config).debug is False

        async with ctx.scope("branch2"):
            ctx.variable(Counter(value=20))
            ctx.variable(Config(debug=True))
            # Doesn't see Metrics from branch1
            assert ctx.variable(Metrics) is None

        # Root now has branch2's values
        assert ctx.variable(Counter).value == 20
        assert ctx.variable(Config).debug is True
        # Still has Metrics from branch1
        assert ctx.variable(Metrics).requests == 100


@mark.asyncio
async def test_variable_immutability():
    async with ctx.scope("test"):
        # Set initial counter
        original = Counter(value=42)
        ctx.variable(original)

        # Retrieve and try to modify (should create new instance)
        counter = ctx.variable(Counter)
        updated = counter.updated(value=100)

        # Original should be unchanged
        assert original.value == 42
        assert counter.value == 42
        assert updated.value == 100

        # Update in context
        ctx.variable(updated)

        # Now context has new value
        assert ctx.variable(Counter).value == 100


@mark.asyncio
async def test_concurrent_variable_updates():
    async with ctx.scope("test"):
        ctx.variable(Counter(value=0))
        ctx.variable(Metrics(requests=0, errors=0))

        update_count = 100

        async def updater(updates: int):
            for i in range(updates):
                # Get current values
                counter = ctx.variable(Counter, default=Counter())
                metrics = ctx.variable(Metrics, default=Metrics())

                # Update values
                ctx.variable(counter.updated(value=counter.value + 1))
                ctx.variable(
                    metrics.updated(
                        requests=metrics.requests + 1,
                        errors=metrics.errors + (1 if i % 10 == 0 else 0),
                    )
                )

                if i % 10 == 0:
                    await asyncio.sleep(0)  # Yield control

        # Run updater
        await updater(update_count)

        # Verify final values
        assert ctx.variable(Counter).value == update_count
        assert ctx.variable(Metrics).requests == update_count
        assert ctx.variable(Metrics).errors == 10  # Every 10th update


@mark.asyncio
async def test_variables_with_none_values():
    async with ctx.scope("test"):
        # Test with None as a field value
        class OptionalConfig(State):
            name: str | None = None
            value: int = 0

        ctx.variable(OptionalConfig(name=None, value=42))
        config = ctx.variable(OptionalConfig)
        assert config.name is None
        assert config.value == 42

        # Update with non-None
        ctx.variable(config.updated(name="test"))
        config = ctx.variable(OptionalConfig)
        assert config.name == "test"


@mark.asyncio
async def test_variable_type_replacement():
    async with ctx.scope("test"):
        # Set Counter
        ctx.variable(Counter(value=100))

        # Same type replaces
        ctx.variable(Counter(value=200))
        assert ctx.variable(Counter).value == 200

        # Different instance of same type
        new_counter = Counter(value=300)
        ctx.variable(new_counter)
        assert ctx.variable(Counter).value == 300
