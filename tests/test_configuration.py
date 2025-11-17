from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from haiway import ctx
from haiway.helpers.configuration import (
    Configuration,
    ConfigurationInvalid,
    ConfigurationRepository,
)
from haiway.types import BasicValue


class MissingRequiredConfiguration(Configuration):
    required: str


class ContextualConfiguration(Configuration):
    value: str


class DefaultedConfiguration(Configuration):
    host: str = "localhost"
    port: int = 9000


@pytest.mark.asyncio
async def test_invalid_data_raises_configuration_invalid() -> None:
    async def load_invalid(
        config: type[MissingRequiredConfiguration],
        identifier: str,
        **extra: Any,
    ) -> MissingRequiredConfiguration | None:
        raise ConfigurationInvalid(identifier=identifier, reason="required")

    repo = ConfigurationRepository(loading=load_invalid)

    async with ctx.scope("config-invalid", repo):
        with pytest.raises(ConfigurationInvalid) as excinfo:
            await MissingRequiredConfiguration.load()

    assert excinfo.value.identifier == MissingRequiredConfiguration.__qualname__
    assert "required" in excinfo.value.reason


@pytest.mark.asyncio
async def test_required_load_prefers_contextual_state() -> None:
    async def missing(
        config: type[ContextualConfiguration],
        identifier: str,
        **extra: Any,
    ) -> Mapping[str, BasicValue] | None:
        return None

    repo = ConfigurationRepository(loading=missing)
    contextual = ContextualConfiguration(value="from-ctx")

    async with ctx.scope("configuration-repo", repo):
        async with ctx.scope("configuration-context", contextual):
            loaded = await ContextualConfiguration.load(required=True)

    assert loaded is contextual


@pytest.mark.asyncio
async def test_required_load_instantiates_defaults_when_context_missing() -> None:
    async def missing(
        config: type[DefaultedConfiguration],
        identifier: str,
        **extra: Any,
    ) -> Mapping[str, BasicValue] | None:
        return None

    repo = ConfigurationRepository(loading=missing)

    async with ctx.scope("configuration-defaults", repo):
        loaded = await DefaultedConfiguration.load(required=True)

    assert isinstance(loaded, DefaultedConfiguration)
    assert loaded.host == "localhost"
    assert loaded.port == 9000
