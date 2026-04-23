from pytest import MonkeyPatch, mark

from haiway import ctx
from haiway.opentelemetry import OpenTelemetry


@mark.asyncio
async def test_autoconfigure_allows_using_global_providers(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(OpenTelemetry, "service", OpenTelemetry.service)
    monkeypatch.setattr(OpenTelemetry, "version", OpenTelemetry.version)
    monkeypatch.setattr(OpenTelemetry, "environment", OpenTelemetry.environment)
    monkeypatch.setattr(OpenTelemetry, "_logger", OpenTelemetry._logger)

    OpenTelemetry.autoconfigure(
        service="test-service",
        version="1.2.3",
        environment="test",
    )

    async with ctx.scope(
        "root",
        observability=OpenTelemetry.observability(),
    ):
        ctx.log_info("configured through global providers")
        ctx.record_info(
            metric="requests.total",
            value=1,
            kind="counter",
        )

    assert OpenTelemetry.service == "test-service"
    assert OpenTelemetry.version == "1.2.3"
    assert OpenTelemetry.environment == "test"
