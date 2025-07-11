import asyncio
from collections.abc import Iterable
from contextlib import asynccontextmanager

from pytest import mark, raises

from haiway import State, ctx
from haiway.context import ContextPresets
from haiway.context.disposables import Disposable
from haiway.context.presets import ContextPresetsRegistry


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
    preset = ContextPresets(
        name="test",
        _state=[ConfigState(api_url="https://api.test.com")],
    )
    assert preset.name == "test"


def test_preset_with_state():
    preset = ContextPresets(name="test")
    updated = preset.with_state(
        ConfigState(api_url="https://api.test.com"),
        DatabaseState(connection_string="sqlite:///:memory:"),
    )

    assert updated.name == "test"
    assert preset is not updated  # Immutable


def test_preset_with_disposable():
    preset = ContextPresets(name="test")

    async def connection_factory() -> Disposable:
        return create_connection_disposable("test")

    updated = preset.with_disposable(connection_factory)
    assert updated.name == "test"
    assert preset is not updated  # Immutable


def test_preset_extended():
    base = ContextPresets(
        name="base",
        _state=[ConfigState(api_url="https://api.test.com")],
    )

    extension = ContextPresets(
        name="extended",
        _state=[DatabaseState(connection_string="sqlite:///:memory:")],
    )

    combined = base.extended(extension)
    assert combined.name == "base"  # Keeps original name
    assert base is not extension  # Immutable
    assert base is not combined  # Immutable


@mark.asyncio
async def test_preset_prepare_with_static_state():
    preset = ContextPresets(
        name="test",
        _state=[
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

    preset = ContextPresets(
        name="test",
        _state=[state_factory],
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
    async def multi_state_factory() -> Iterable[State]:
        return [
            ConfigState(api_url="https://api.test.com"),
            DatabaseState(connection_string="sqlite:///:memory:"),
            CacheState(ttl=7200),
        ]

    preset = ContextPresets(
        name="test",
        _state=[multi_state_factory],
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

    preset = ContextPresets(
        name="test",
        _disposables=[connection_factory],
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

    preset = ContextPresets(
        name="test",
        _disposables=[connection1_factory, connection2_factory, multi_disposable_factory],
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

    preset = ContextPresets(
        name="test",
        _state=[ConfigState(api_url="https://api.test.com")],
        _disposables=[connection_factory],
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
    preset1 = ContextPresets(name="db", _state=[DatabaseState(connection_string="test")])
    preset2 = ContextPresets(name="cache", _state=[CacheState()])

    registry = ContextPresetsRegistry([preset1, preset2])

    assert registry.select("db") is preset1
    assert registry.select("cache") is preset2
    assert registry.select("unknown") is None


def test_registry_immutable():
    registry = ContextPresetsRegistry([])

    with raises(AttributeError):
        registry._presets = {}  # type: ignore

    with raises(AttributeError):
        del registry._presets  # type: ignore


@mark.asyncio
async def test_scope_with_preset():
    preset = ContextPresets(
        name="test",
        _state=[ConfigState(api_url="https://api.test.com")],
    )

    with ctx.presets(preset):
        async with ctx.scope("test"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://api.test.com"


@mark.asyncio
async def test_scope_preset_with_override():
    preset = ContextPresets(
        name="test",
        _state=[ConfigState(api_url="https://api.test.com", timeout=30)],
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


def test_sync_scope_with_preset_fails():
    preset = ContextPresets(
        name="test",
        _state=[ConfigState(api_url="https://api.test.com")],
    )

    with ctx.presets(preset):
        with raises(AssertionError, match="Can't enter synchronous context with presets"):
            with ctx.scope("test"):
                pass


@mark.asyncio
async def test_preset_with_async_state_factory():
    fetch_count = 0

    async def fetch_config() -> ConfigState:
        nonlocal fetch_count
        fetch_count += 1
        # Simulate async operation
        await asyncio.sleep(0.01)
        return ConfigState(api_url=f"https://api{fetch_count}.test.com")

    preset = ContextPresets(
        name="dynamic",
        _state=[fetch_config],
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

    preset = ContextPresets(
        name="connections",
        _disposables=[tracked_connection_factory],
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
    preset1 = ContextPresets(
        name="outer",
        _state=[ConfigState(api_url="https://outer.com")],
    )

    preset2 = ContextPresets(
        name="inner",
        _state=[ConfigState(api_url="https://inner.com")],
    )

    preset3 = ContextPresets(
        name="outer",  # Same name as preset1
        _state=[ConfigState(api_url="https://inner-override.com")],
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

    async def multi_state() -> Iterable[State]:
        return [
            DatabaseState(connection_string="dynamic-db"),
            CacheState(ttl=1800),
        ]

    async def connection_factory() -> Disposable:
        return create_connection_disposable("preset-conn")

    preset = ContextPresets(
        name="mixed",
        _state=[
            ConfigState(api_url="https://static.com"),  # Static
            dynamic_config,  # Async factory
            multi_state,  # Multi-state factory
        ],
        _disposables=[connection_factory],
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
