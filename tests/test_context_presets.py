import asyncio
from collections.abc import Iterable
from contextlib import asynccontextmanager

from pytest import mark, raises

from haiway import State, ctx
from haiway.context.disposables import Disposable
from haiway.context.presets import ContextPresets, ContextPresetsRegistry


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
    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://api.test.com"),
    )
    assert preset.name == "test"


def test_preset_with_state():
    preset = ContextPresets.of("test")
    updated = preset.with_state(
        ConfigState(api_url="https://api.test.com"),
        DatabaseState(connection_string="sqlite:///:memory:"),
    )

    assert updated.name == "test"
    assert preset is not updated  # Immutable


def test_preset_with_disposable():
    preset = ContextPresets.of("test")

    def connection_factory() -> Disposable:
        return create_connection_disposable("test")

    updated = preset.with_disposables(connection_factory)
    assert updated.name == "test"
    assert preset is not updated  # Immutable


def test_preset_extended():
    base = ContextPresets.of(
        "base",
        ConfigState(api_url="https://api.test.com"),
    )

    extension = ContextPresets.of(
        "extended",
        DatabaseState(connection_string="sqlite:///:memory:"),
    )

    combined = base.extended(extension)
    assert combined.name == "base"  # Keeps original name
    assert base is not extension  # Immutable
    assert base is not combined  # Immutable


@mark.asyncio
async def test_preset_prepare_with_static_state():
    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://api.test.com"),
        DatabaseState(connection_string="sqlite:///:memory:"),
    )

    disposables = preset.resolve()
    async with disposables as states_iter:
        states = list(states_iter)

    # Should have 2 states wrapped in DisposableState
    assert len(states) == 2
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

    preset = ContextPresets.of(
        "test",
        state_factory,
    )

    # First prepare
    disposables1 = preset.resolve()
    async with disposables1 as states1_iter:
        states1 = list(states1_iter)
    assert len(states1) == 1
    assert states1[0].api_url == "https://api1.test.com"

    assert call_count == 1

    # Second prepare - should create new instance
    disposables2 = preset.resolve()
    async with disposables2 as states2_iter:
        states2 = list(states2_iter)
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

    preset = ContextPresets.of(
        "test",
        multistate_factory,
    )

    disposables = preset.resolve()
    async with disposables as states_iter:
        states = list(states_iter)

    assert len(states) == 3
    state_types = {type(s) for s in states}
    assert state_types == {ConfigState, DatabaseState, CacheState}


@mark.asyncio
async def test_preset_prepare_with_disposables():
    def connection_factory() -> Disposable:
        return create_connection_disposable("test")

    preset = ContextPresets.of(
        "test",
        disposables=(connection_factory,),
    )

    # Use the disposables in a scope to get state
    disposables = preset.resolve()
    async with disposables as states:
        async with ctx.scope("test_scope", *states):
            # Connection should be available in context
            conn_state = ctx.state(ConnectionState)
            assert conn_state.name == "test"
            assert not conn_state.closed


@mark.asyncio
async def test_preset_prepare_with_multiple_disposables():
    def connection1_factory() -> Disposable:
        return create_connection_disposable("conn1")

    def connection2_factory() -> Disposable:
        return create_connection_disposable("conn2")

    def connection3_factory() -> Disposable:
        return create_connection_disposable("conn3")

    def connection4_factory() -> Disposable:
        return create_connection_disposable("conn4")

    preset = ContextPresets.of(
        "test",
        disposables=(
            connection1_factory,
            connection2_factory,
            connection3_factory,
            connection4_factory,
        ),
    )

    disposables = preset.resolve()
    async with disposables as states_iter:
        states = list(states_iter)

    # Should have 4 ConnectionState instances
    assert len(states) == 4
    assert all(isinstance(s, ConnectionState) for s in states)

    names = {s.name for s in states}
    assert names == {"conn1", "conn2", "conn3", "conn4"}


@mark.asyncio
async def test_preset_prepare_mixed_state_and_disposables():
    def connection_factory() -> Disposable:
        return create_connection_disposable("test")

    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://api.test.com"),
        disposables=(connection_factory,),
    )

    # The preset should have wrapped state in DisposableState
    # plus the connection disposable
    disposables = preset.resolve()
    async with disposables as states:
        async with ctx.scope("test_scope", *states):
            # Both states should be available
            config = ctx.state(ConfigState)
            assert config.api_url == "https://api.test.com"

            conn = ctx.state(ConnectionState)
            assert conn.name == "test"


def test_registry_creation():
    preset1 = ContextPresets.of("db", DatabaseState(connection_string="test"))
    preset2 = ContextPresets.of("cache", CacheState())

    with ContextPresetsRegistry([preset1, preset2]):
        assert ContextPresetsRegistry.select("db") is preset1
        assert ContextPresetsRegistry.select("cache") is preset2
        assert ContextPresetsRegistry.select("unknown") is None


def test_registry_immutable():
    registry = ContextPresetsRegistry([])

    with raises(AttributeError):
        registry._presets = {}  # type: ignore

    with raises(AttributeError):
        del registry._presets  # type: ignore


@mark.asyncio
async def test_scope_with_preset():
    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://api.test.com"),
    )

    with ctx.presets(preset):
        async with ctx.scope("test"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://api.test.com"


@mark.asyncio
async def test_scope_preset_with_override():
    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://api.test.com", timeout=30),
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
async def test_scope_preset_overrides_parent_state():
    parent = ConfigState(api_url="https://parent.com", timeout=10)
    preset = ContextPresets.of(
        "child",
        ConfigState(api_url="https://preset.com", timeout=20),
        CacheState(ttl=1200),
    )

    async with ctx.scope("parent", parent):
        with ctx.presets(preset):
            async with ctx.scope("child"):
                config = ctx.state(ConfigState)
                cache = ctx.state(CacheState)

                assert config.api_url == "https://preset.com"
                assert config.timeout == 20
                assert cache.ttl == 1200


@mark.asyncio
async def test_preset_with_async_state_factory():
    fetch_count = 0

    async def fetch_config() -> ConfigState:
        nonlocal fetch_count
        fetch_count += 1
        # Simulate async operation
        await asyncio.sleep(0.01)
        return ConfigState(api_url=f"https://api{fetch_count}.test.com")

    preset = ContextPresets.of(
        "dynamic",
        fetch_config,
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

    def tracked_connection_factory() -> Disposable:
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

    preset = ContextPresets.of(
        "connections",
        disposables=(tracked_connection_factory,),
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
    preset1 = ContextPresets.of(
        "outer",
        ConfigState(api_url="https://outer.com"),
    )

    preset2 = ContextPresets.of(
        "inner",
        ConfigState(api_url="https://inner.com"),
    )

    preset3 = ContextPresets.of(
        "outer",  # Same name as preset1
        ConfigState(api_url="https://inner-override.com"),
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

    def connection_factory() -> Disposable:
        return create_connection_disposable("preset-conn")

    preset = ContextPresets.of(
        "mixed",
        ConfigState(api_url="https://static.com"),  # Static
        dynamic_config,  # Async factory
        multistate,  # Multi-state factory
        disposables=(connection_factory,),
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
    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://direct.com", timeout=45),
    )

    # Direct preset usage
    async with ctx.scope(preset):
        config = ctx.state(ConfigState)
        assert config.api_url == "https://direct.com"
        assert config.timeout == 45


@mark.asyncio
async def test_direct_preset_with_explicit_state_override():
    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://preset.com", timeout=30),
        DatabaseState(connection_string="preset-db"),
    )

    # Explicit state should override preset state
    async with ctx.scope(
        preset,
        ConfigState(api_url="https://override.com", timeout=60),
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

    def create_disposable() -> Disposable:
        nonlocal call_count
        call_count += 1
        return create_connection_disposable(f"conn{call_count}")

    preset = ContextPresets.of(
        "test",
        ConfigState(api_url="https://preset.com"),
        disposables=(create_disposable,),
    )

    async with ctx.scope(preset):
        config = ctx.state(ConfigState)
        assert config.api_url == "https://preset.com"

        conn = ctx.state(ConnectionState)
        assert conn.name == "conn1"
        assert not conn.closed

    # Verify disposable was called
    assert call_count == 1


@mark.asyncio
async def test_direct_preset_vs_registry():
    registry_preset = ContextPresets.of(
        "main",  # Same name as scope
        ConfigState(api_url="https://registry.com"),
    )

    direct_preset = ContextPresets.of(
        "different",
        ConfigState(api_url="https://direct.com"),
    )

    # Registry preset would normally be selected by name
    with ctx.presets(registry_preset):
        # But direct preset should take precedence
        async with ctx.scope(direct_preset):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://direct.com"


@mark.asyncio
async def test_direct_preset_none_falls_back_to_registry():
    registry_preset = ContextPresets.of(
        "fallback",
        ConfigState(api_url="https://registry.com"),
    )

    with ctx.presets(registry_preset):
        # No preset parameter, should use registry
        async with ctx.scope("fallback"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://registry.com"

        # Explicit preset=None should also use registry
        async with ctx.scope("fallback"):
            config = ctx.state(ConfigState)
            assert config.api_url == "https://registry.com"
