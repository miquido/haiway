from typing import Any, cast

import pytest

from haiway import ctx
from haiway.helpers.configuration import (
    Configuration,
    ConfigurationInvalid,
    ConfigurationMissing,
    ConfigurationRepository,
)


class MissingRequiredConfiguration(Configuration):
    required: str


class ContextualConfiguration(Configuration):
    value: str


class DefaultedConfiguration(Configuration):
    host: str = "localhost"
    port: int = 9000


@pytest.mark.asyncio
async def test_invalid_data_raises_configuration_invalid() -> None:
    async def load_invalid[Config: Configuration](
        config: type[Config],
        identifier: str,
        **extra: Any,
    ) -> Config | None:
        raise ConfigurationInvalid(identifier=identifier, reason="required")

    repo = ConfigurationRepository(loading=load_invalid)

    async with ctx.scope("config-invalid", repo):
        with pytest.raises(ConfigurationInvalid) as excinfo:
            await MissingRequiredConfiguration.load()

    assert excinfo.value.identifier == MissingRequiredConfiguration.__qualname__
    assert "required" in excinfo.value.reason


@pytest.mark.asyncio
async def test_required_load_prefers_contextual_state() -> None:
    async def missing[Config: Configuration](
        config: type[Config],
        identifier: str,
        **extra: Any,
    ) -> Config | None:
        return None

    repo = ConfigurationRepository(loading=missing)
    contextual = ContextualConfiguration(value="from-ctx")

    async with ctx.scope("configuration-repo", repo):
        async with ctx.scope("configuration-context", contextual):
            loaded = await ContextualConfiguration.load(required=True)

    assert loaded is contextual


@pytest.mark.asyncio
async def test_required_load_instantiates_defaults_when_context_missing() -> None:
    async def missing[Config: Configuration](
        config: type[Config],
        identifier: str,
        **extra: Any,
    ) -> Config | None:
        return None

    repo = ConfigurationRepository(loading=missing)

    async with ctx.scope("configuration-defaults", repo):
        loaded = await DefaultedConfiguration.load(required=True)

    assert isinstance(loaded, DefaultedConfiguration)
    assert loaded.host == "localhost"
    assert loaded.port == 9000


class DatabaseConfiguration(Configuration):
    host: str = "localhost"
    port: int = 5432


class CacheConfiguration(Configuration):
    ttl: int = 60


class StrictConfiguration(Configuration):
    token: str


def test_configuration_exceptions_expose_details() -> None:
    missing = ConfigurationMissing(identifier="db")
    assert missing.identifier == "db"
    assert "db" in str(missing)

    invalid = ConfigurationInvalid(identifier="db", reason="bad port")
    assert invalid.identifier == "db"
    assert invalid.reason == "bad port"
    assert "db" in str(invalid)
    assert "bad port" in str(invalid)


@pytest.mark.asyncio
async def test_default_repository_loads_nothing() -> None:
    async with ctx.scope("config-default-repo"):
        assert await ConfigurationRepository.configurations() == ()
        assert await DatabaseConfiguration.load() is None
        await ConfigurationRepository.define(DatabaseConfiguration())  # no-op
        await ConfigurationRepository.remove(DatabaseConfiguration)  # no-op
        assert await DatabaseConfiguration.load() is None


@pytest.mark.asyncio
async def test_volatile_loads_by_class_name_and_custom_identifier() -> None:
    default = DatabaseConfiguration(host="default")
    production = DatabaseConfiguration(host="prod")
    repo = ConfigurationRepository.volatile(default, production=production)

    async with ctx.scope("config-volatile", repo):
        assert await DatabaseConfiguration.load() is default
        assert await DatabaseConfiguration.load("production") is production
        assert await DatabaseConfiguration.load(identifier="production") is production
        assert await ConfigurationRepository.load(DatabaseConfiguration) is default
        assert await CacheConfiguration.load() is None


@pytest.mark.asyncio
async def test_load_returns_default_when_missing_but_prefers_stored() -> None:
    stored = DatabaseConfiguration(host="stored")
    fallback = DatabaseConfiguration(host="fallback")
    repo = ConfigurationRepository.volatile(stored)

    async with ctx.scope("config-default", repo):
        assert await DatabaseConfiguration.load(default=fallback) is stored
        assert await DatabaseConfiguration.load("other", default=fallback) is fallback


@pytest.mark.asyncio
async def test_required_load_raises_missing_without_contextual_fallback() -> None:
    repo = ConfigurationRepository.volatile()

    async with ctx.scope("config-required-missing", repo):
        with pytest.raises(ConfigurationMissing) as excinfo:
            await StrictConfiguration.load(required=True)

    assert excinfo.value.identifier == StrictConfiguration.__qualname__


@pytest.mark.asyncio
async def test_required_load_prefers_repository_over_context() -> None:
    stored = DatabaseConfiguration(host="stored")
    contextual = DatabaseConfiguration(host="contextual")

    async with ctx.scope("config-required-repo", ConfigurationRepository.volatile(stored)):
        async with ctx.scope("config-required-ctx", contextual):
            assert await DatabaseConfiguration.load(required=True) is stored


@pytest.mark.asyncio
async def test_default_wins_over_required_fallback() -> None:
    fallback = StrictConfiguration(token="fallback")

    async with ctx.scope("config-default-required", ConfigurationRepository.volatile()):
        loaded = await StrictConfiguration.load(default=fallback, required=True)

    assert loaded is fallback


@pytest.mark.asyncio
async def test_extra_arguments_are_forwarded_to_loader() -> None:
    received: dict[str, Any] = {}

    async def loading[Config: Configuration](
        config: type[Config],
        identifier: str,
        **extra: Any,
    ) -> Config | None:
        received["identifier"] = identifier
        received["extra"] = extra
        return cast(Config, DatabaseConfiguration(host="from-loader"))

    repo = ConfigurationRepository(loading=loading)

    async with ctx.scope("config-extra", repo):
        loaded = await DatabaseConfiguration.load("custom", revision=3)

    assert loaded is not None
    assert loaded.host == "from-loader"
    assert received == {"identifier": "custom", "extra": {"revision": 3}}


@pytest.mark.asyncio
async def test_loader_failure_is_wrapped_with_cause() -> None:
    async def loading[Config: Configuration](
        config: type[Config],
        identifier: str,
        **extra: Any,
    ) -> Config | None:
        raise OSError("disk gone")

    repo = ConfigurationRepository(loading=loading)

    async with ctx.scope("config-loader-failure", repo):
        with pytest.raises(ConfigurationInvalid) as excinfo:
            await DatabaseConfiguration.load("db")

    assert excinfo.value.identifier == "db"
    assert "disk gone" in excinfo.value.reason
    assert isinstance(excinfo.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_volatile_type_mismatch_is_reported_as_invalid() -> None:
    repo = ConfigurationRepository.volatile(shared=CacheConfiguration())

    async with ctx.scope("config-type-mismatch", repo):
        with pytest.raises(ConfigurationInvalid) as excinfo:
            await DatabaseConfiguration.load("shared")

    assert excinfo.value.identifier == "shared"
    assert isinstance(excinfo.value.__cause__, TypeError)


@pytest.mark.asyncio
async def test_volatile_define_and_remove_roundtrip() -> None:
    repo = ConfigurationRepository.volatile()
    db = DatabaseConfiguration(host="defined")
    named = CacheConfiguration(ttl=5)

    async with ctx.scope("config-define", repo):
        await ConfigurationRepository.define(db)
        await ConfigurationRepository.define("short-cache", named)

        assert await DatabaseConfiguration.load() is db
        assert await CacheConfiguration.load("short-cache") is named
        assert set(await ConfigurationRepository.configurations()) == {
            DatabaseConfiguration.__qualname__,
            "short-cache",
        }

        await ConfigurationRepository.remove(DatabaseConfiguration)
        await ConfigurationRepository.remove("short-cache")

        assert await DatabaseConfiguration.load() is None
        assert await CacheConfiguration.load("short-cache") is None
        assert await ConfigurationRepository.configurations() == ()


@pytest.mark.asyncio
async def test_define_overwrites_existing_value() -> None:
    repo = ConfigurationRepository.volatile(DatabaseConfiguration(host="old"))

    async with ctx.scope("config-overwrite", repo):
        replacement = DatabaseConfiguration(host="new")
        await ConfigurationRepository.define(replacement)

        assert await DatabaseConfiguration.load() is replacement


@pytest.mark.asyncio
async def test_volatile_remove_of_unknown_identifier_fails() -> None:
    repo = ConfigurationRepository.volatile()

    async with ctx.scope("config-remove-unknown", repo):
        with pytest.raises(KeyError):
            await ConfigurationRepository.remove("absent")


@pytest.mark.asyncio
async def test_configurations_can_be_filtered_by_type() -> None:
    repo = ConfigurationRepository.volatile(
        DatabaseConfiguration(),
        replica=DatabaseConfiguration(host="replica"),
        cache=CacheConfiguration(),
    )

    async with ctx.scope("config-listing", repo):
        assert set(await ConfigurationRepository.configurations()) == {
            DatabaseConfiguration.__qualname__,
            "replica",
            "cache",
        }
        assert set(await ConfigurationRepository.configurations(DatabaseConfiguration)) == {
            DatabaseConfiguration.__qualname__,
            "replica",
        }
        assert await ConfigurationRepository.configurations(StrictConfiguration) == ()


@pytest.mark.asyncio
async def test_define_and_remove_failures_propagate_unchanged() -> None:
    async def defining(identifier: str, value: Configuration, **extra: Any) -> None:
        raise PermissionError("read-only")

    async def removing(identifier: str, **extra: Any) -> None:
        raise PermissionError("read-only")

    repo = ConfigurationRepository(defining=defining, removing=removing)

    async with ctx.scope("config-failures", repo):
        with pytest.raises(PermissionError):
            await ConfigurationRepository.define(DatabaseConfiguration())
        with pytest.raises(PermissionError):
            await ConfigurationRepository.remove("db")


@pytest.mark.asyncio
async def test_define_remove_and_list_forward_extra_arguments() -> None:
    calls: list[tuple[str, Any]] = []

    async def listing(config: type[Configuration] | None, **extra: Any) -> tuple[str, ...]:
        calls.append(("list", (config, extra)))
        return ("a",)

    async def defining(identifier: str, value: Configuration, **extra: Any) -> None:
        calls.append(("define", (identifier, extra)))

    async def removing(identifier: str, **extra: Any) -> None:
        calls.append(("remove", (identifier, extra)))

    repo = ConfigurationRepository(listing=listing, defining=defining, removing=removing)

    async with ctx.scope("config-forwarding", repo):
        assert await ConfigurationRepository.configurations(CacheConfiguration, tenant="x") == (
            "a",
        )
        await ConfigurationRepository.define(CacheConfiguration(), tenant="x")
        await ConfigurationRepository.define("custom", CacheConfiguration(), tenant="y")
        await ConfigurationRepository.remove(CacheConfiguration, tenant="z")
        await ConfigurationRepository.remove("custom")

    assert calls == [
        ("list", (CacheConfiguration, {"tenant": "x"})),
        ("define", (CacheConfiguration.__qualname__, {"tenant": "x"})),
        ("define", ("custom", {"tenant": "y"})),
        ("remove", (CacheConfiguration.__qualname__, {"tenant": "z"})),
        ("remove", ("custom", {})),
    ]


def test_volatile_has_source_meta_and_rejects_non_configurations() -> None:
    repo = ConfigurationRepository.volatile()
    assert repo.meta.get("source") == "volatile"

    with pytest.raises(AssertionError):
        ConfigurationRepository.volatile(object())  # pyright: ignore[reportArgumentType]
