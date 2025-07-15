import asyncio
from collections.abc import Iterable
from contextlib import asynccontextmanager

from pytest import mark, raises

from haiway import State, ctx
from haiway.context.disposables import Disposable
from haiway.context.presets import ContextPreset, ContextPresetRegistryContext


# Test state classes
class ConfigState(State):
    api_url: str
    timeout: int = 30


class DatabaseState(State):
    connection_string: str
    pool_size: int = 10


class CacheState(State):
    ttl: int = 3600
    max_size: int = 1000


class ConnectionState(State):
    name: str
    closed: bool = False


def create_connection_disposable(name: str) -> Disposable:
    @asynccontextmanager
    async def disposable():
        try:
            yield ConnectionState(
                name=name,
                closed=False,
            )

        finally:
            # Connection cleanup would happen here
            pass

    return disposable()


def test_preset_creation():
    """Test creating a preset with state."""
    preset = ContextPreset(
        name="test",
        state=[ConfigState(api_url="https://api.test.com")],
    )
    assert preset.name == "test"


def test_preset_with_state():
    preset = ContextPreset(name="test")
    updated = preset.with_state(
        ConfigState(api_url="https://api.test.com"),
        DatabaseState(connection_string="sqlite:///:memory:"),
    )

    assert updated.name == "test"
    assert preset is not updated  # Immutable


def test_preset_with_disposable():
    preset = ContextPreset(name="test")

    async def connection_factory() -> Disposable:
        return create_connection_disposable("test")

    updated = preset.with_disposable(connection_factory)
    assert updated.name == "test"
    assert preset is not updated  # Immutable


def test_preset_extended():
    base = ContextPreset(
        name="base",
        state=[ConfigState(api_url="https://api.test.com")],
    )

    extension = ContextPreset(
        name="extended",
        state=[DatabaseState(connection_string="sqlite:///:memory:")],
    )

    combined = base.extended(extension)
    assert combined.name == "base"  # Keeps original name
    assert base is not extension  # Immutable
    assert base is not combined  # Immutable


@mark.asyncio
async def test_preset_prepare_with_static_state():
    preset = ContextPreset(
        name="test",
        state=[
            ConfigState(api_url="https://api.test.com"),
            DatabaseState(connection_string="sqlite:///:memory:"),
        ],
    )

    disposables = await preset.prepare()
    states = await disposables.prepare()

    # Should have 2 states wrapped in DisposableState
    assert len(list(states)) == 2
    state_types = {type(s) for s in states}
    assert ConfigState in state_types
    assert DatabaseState in state_types


@mark.asyncio
async def test_preset_prepare_with_state_factory():
    call_count = 0

    async def state_factory() -> ConfigState:
        nonlocal call_count
        call_count += 1
        return ConfigState(api_url=f"https://api{call_count}.test.com")

    preset = ContextPreset(
        name="test",
        state=[state_factory],
    )

    # First prepare
    disposables1 = await preset.prepare()
    states1 = list(await disposables1.prepare())
    assert len(states1) == 1
    assert states1[0].api_url == "https://api1.test.com"

    assert call_count == 1

    # Second prepare - should create new instance
    disposables2 = await preset.prepare()
    states2 = list(await disposables2.prepare())
    assert len(states2) == 1
    assert states2[0].api_url == "https://api2.test.com"

    assert call_count == 2


@mark.asyncio
async def test_preset_prepare_with_multiple_states_factory():
    async def multistate_factory() -> Iterable[State]:
        return [
            ConfigState(api_url="https://api.test.com"),
            DatabaseState(connection_string="sqlite:///:memory:"),
            CacheState(ttl=7200),
        ]

    preset = ContextPreset(
        name="test",
        state=[multistate_factory],
    )

    disposables = await preset.prepare()
    states = list(await disposables.prepare())

    assert len(states) == 3
    state_types = {type(s) for s in states}
    assert state_types == {ConfigState, DatabaseState, CacheState}


@mark.asyncio
async def test_preset_prepare_with_disposables():
    async def connection_factory() -> Disposable:
        return create_connection_disposable("test")

    preset = ContextPreset(
        name="test",
        disposables=[connection_factory],
    )

    disposables = await preset.prepare()

    # Use the disposables in a scope to get state
    async with ctx.scope("test_scope", disposables=disposables):
        # Connection should be available in context
        conn_state = ctx.state(ConnectionState)
        assert conn_state.name == "test"
        assert not conn_state.closed


@mark.asyncio
async def test_preset_prepare_with_multiple_disposables():
    async def connection1_factory() -> Disposable:
        return create_connection_disposable("conn1")

    async def connection2_factory() -> Disposable:
        return create_connection_disposable("conn2")

    # Factory returning multiple disposables
    async def multi_disposable_factory() -> Iterable[Disposable]:
        return [
            create_connection_disposable("conn3"),
            create_connection_disposable("conn4"),
        ]

    preset = ContextPreset(
        name="test",
        disposables=[connection1_factory, connection2_factory, multi_disposable_factory],
    )

    disposables = await preset.prepare()
    states = list(await disposables.prepare())

    # Should have 4 ConnectionState instances
    assert len(states) == 4
    assert all(isinstance(s, ConnectionState) for s in states)

    names = {s.name for s in states}
    assert names == {"conn1", "conn2", "conn3", "conn4"}


@mark.asyncio
async def test_preset_prepare_mixed_state_and_disposables():
    async def connection_factory() -> Disposable:
        return create_connection_disposable("test")

    preset = ContextPreset(
        name="test",
        state=[ConfigState(api_url="https://api.test.com")],
        disposables=[connection_factory],
    )

    disposables = await preset.prepare()

    # The preset should have wrapped state in DisposableState
    # plus the connection disposable
    async with ctx.scope("test_scope", disposables=disposables):
        # Both states should be available
        config = ctx.state(ConfigState)
        assert config.api_url == "https://api.test.com"

        conn = ctx.state(ConnectionState)
        assert conn.name == "test"


def test_registry_creation():
    preset1 = ContextPreset(name="db", state=[DatabaseState(connection_string="test")])
    preset2 = ContextPreset(name="cache", state=[CacheState()])

    with ContextPresetRegistryContext([preset1, preset2]):
        assert ContextPresetRegistryContext.select("db") is preset1
        assert ContextPresetRegistryContext.select("cache") is preset2
        assert ContextPresetRegistryContext.select("unknown") is None


def test_registry_immutable():
    registry = ContextPresetRegistryContext([])

    with raises(AttributeError):
        registry._presets = {}  # type: ignore

    with raises(AttributeError):
        del registry._presets  # type: ignore


@mark.asyncio
async def test_scope_with_preset():
    preset = ContextPreset(
        name="test",
        state=[ConfigState(api_url="https://api.test.com")],
    )

    with ctx.presets(preset):
        async with ctx.scope("test"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://api.test.com"


@mark.asyncio
async def test_scope_preset_with_override():
    preset = ContextPreset(
        name="test",
        state=[ConfigState(api_url="https://api.test.com", timeout=30)],
    )

    with ctx.presets(preset):
        async with ctx.scope("test", ConfigState(api_url="https://override.com", timeout=60)):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://override.com"
            assert config.timeout == 60


@mark.asyncio
async def test_scope_preset_not_found():
    async with ctx.scope("nonexistent", ConfigState(api_url="https://api.test.com")):
        config = ctx.state(ConfigState)
        assert config.api_url == "https://api.test.com"


@mark.asyncio
async def test_preset_with_async_state_factory():
    fetch_count = 0

    async def fetch_config() -> ConfigState:
        nonlocal fetch_count
        fetch_count += 1
        # Simulate async operation
        await asyncio.sleep(0.01)
        return ConfigState(api_url=f"https://api{fetch_count}.test.com")

    preset = ContextPreset(
        name="dynamic",
        state=[fetch_config],
    )

    with ctx.presets(preset):
        # First scope
        async with ctx.scope("dynamic"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://api1.test.com"

        # Second scope - should fetch again
        async with ctx.scope("dynamic"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://api2.test.com"

    assert fetch_count == 2


@mark.asyncio
async def test_preset_with_disposable_lifecycle():
    connections = []

    async def tracked_connection_factory() -> Disposable:
        @asynccontextmanager
        async def disposable():
            conn_name = f"conn{len(connections)}"
            connections.append(conn_name)
            try:
                yield ConnectionState(
                    name=conn_name,
                    closed=False,
                )

            finally:
                # Mark as closed in our tracking
                pass

        return disposable()

    preset = ContextPreset(
        name="connections",
        disposables=[tracked_connection_factory],
    )

    with ctx.presets(preset):
        # First scope
        async with ctx.scope("connections"):
            conn1 = ctx.state(ConnectionState)
            assert conn1.name == "conn0"
            assert not conn1.closed

        # Second scope - new connection
        async with ctx.scope("connections"):
            conn2 = ctx.state(ConnectionState)
            assert conn2.name == "conn1"
            assert not conn2.closed

    assert len(connections) == 2
    assert connections == ["conn0", "conn1"]


@mark.asyncio
async def test_nested_preset_registries():
    preset1 = ContextPreset(
        name="outer",
        state=[ConfigState(api_url="https://outer.com")],
    )

    preset2 = ContextPreset(
        name="inner",
        state=[ConfigState(api_url="https://inner.com")],
    )

    preset3 = ContextPreset(
        name="outer",  # Same name as preset1
        state=[ConfigState(api_url="https://inner-override.com")],
    )

    with ctx.presets(preset1):
        async with ctx.scope("outer"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://outer.com"

        # Nested registry
        with ctx.presets(preset2, preset3):
            # Inner registry shadows outer
            async with ctx.scope("outer"):
                config = ctx.state(ConfigState)
                assert config.api_url == "https://inner-override.com"

            async with ctx.scope("inner"):
                config = ctx.state(ConfigState)
                assert config.api_url == "https://inner.com"

        # Back to outer registry
        async with ctx.scope("outer"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://outer.com"


@mark.asyncio
async def test_preset_with_mixed_state_sources():
    async def dynamic_config() -> ConfigState:
        await asyncio.sleep(0.01)
        return ConfigState(api_url="https://dynamic.com")

    async def multistate() -> Iterable[State]:
        return [
            DatabaseState(connection_string="dynamic-db"),
            CacheState(ttl=1800),
        ]

    async def connection_factory() -> Disposable:
        return create_connection_disposable("preset-conn")

    preset = ContextPreset(
        name="mixed",
        state=[
            ConfigState(api_url="https://static.com"),  # Static
            dynamic_config,  # Async factory
            multistate,  # Multi-state factory
        ],
        disposables=[connection_factory],
    )

    with ctx.presets(preset):
        async with ctx.scope("mixed", CacheState(ttl=900)):  # Override one state
            # Should have all states available
            static_config = ctx.state(ConfigState)
            assert static_config.api_url in ("https://static.com", "https://dynamic.com")

            db = ctx.state(DatabaseState)
            assert db.connection_string == "dynamic-db"

            cache = ctx.state(CacheState)
            # Explicit state should override preset state
            assert cache.ttl == 900  # From explicit state, overrides preset

            conn = ctx.state(ConnectionState)
            assert conn.name == "preset-conn"


@mark.asyncio
async def test_direct_preset_parameter():
    preset = ContextPreset(
        name="test",
        state=[ConfigState(api_url="https://direct.com", timeout=45)],
    )

    # Direct preset usage
    async with ctx.scope("main", preset=preset):
        config = ctx.state(ConfigState)
        assert config.api_url == "https://direct.com"
        assert config.timeout == 45


@mark.asyncio
async def test_direct_preset_with_explicit_state_override():
    preset = ContextPreset(
        name="test",
        state=[
            ConfigState(api_url="https://preset.com", timeout=30),
            DatabaseState(connection_string="preset-db"),
        ],
    )

    # Explicit state should override preset state
    async with ctx.scope(
        "main",
        ConfigState(api_url="https://override.com", timeout=60),
        preset=preset,
    ):
        config = ctx.state(ConfigState)
        assert config.api_url == "https://override.com"
        assert config.timeout == 60

        # Non-overridden state from preset should still be available
        db = ctx.state(DatabaseState)
        assert db.connection_string == "preset-db"


@mark.asyncio
async def test_direct_preset_with_disposables():
    call_count = 0

    async def create_disposable():
        nonlocal call_count
        call_count += 1
        return create_connection_disposable(f"conn{call_count}")

    preset = ContextPreset(
        name="test",
        state=[ConfigState(api_url="https://preset.com")],
        disposables=[create_disposable],
    )

    async with ctx.scope("main", preset=preset):
        config = ctx.state(ConfigState)
        assert config.api_url == "https://preset.com"

        conn = ctx.state(ConnectionState)
        assert conn.name == "conn1"
        assert not conn.closed

    # Verify disposable was called
    assert call_count == 1


@mark.asyncio
async def test_direct_preset_vs_registry():
    registry_preset = ContextPreset(
        name="main",  # Same name as scope
        state=[ConfigState(api_url="https://registry.com")],
    )

    direct_preset = ContextPreset(
        name="different",
        state=[ConfigState(api_url="https://direct.com")],
    )

    # Registry preset would normally be selected by name
    with ctx.presets(registry_preset):
        # But direct preset should take precedence
        async with ctx.scope("main", preset=direct_preset):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://direct.com"


@mark.asyncio
async def test_direct_preset_none_falls_back_to_registry():
    registry_preset = ContextPreset(
        name="fallback",
        state=[ConfigState(api_url="https://registry.com")],
    )

    with ctx.presets(registry_preset):
        # No preset parameter, should use registry
        async with ctx.scope("fallback"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://registry.com"

        # Explicit preset=None should also use registry
        async with ctx.scope("fallback", preset=None):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://registry.com"
