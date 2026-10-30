from collections.abc import Callable
from importlib import import_module
from typing import get_origin

import httpx2
from asyncpg import utils as asyncpg_utils
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from pika import URLParameters
from pytest import mark
from starlette.requests import Request
from typing_extensions import TypedDict


def _typing_extensions() -> None:
    class Sample(TypedDict):
        value: int

    assert get_origin(list[int]) is list
    assert Sample(value=1) == {"value": 1}


def _httpx2() -> None:
    request = httpx2.Request("GET", "http://localhost/path", params={"a": "1"})
    assert request.url.path == "/path"
    assert request.url.query == b"a=1"


def _asyncpg() -> None:
    assert asyncpg_utils._quote_ident("a b") == '"a b"'


def _pika() -> None:
    parameters = URLParameters("amqp://user:pass@localhost:5672/vhost")
    assert parameters.host == "localhost"
    assert parameters.port == 5672
    assert parameters.virtual_host == "vhost"


def _starlette() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert request.method == "GET"
    assert request.url.path == "/"


def _fastapi() -> None:
    app = FastAPI()

    @app.get("/")
    def root() -> None:  # pragma: no cover
        return None

    assert [route.path for route in app.routes if hasattr(route, "path")][-1] == "/"


def _opentelemetry_sdk() -> None:
    provider = TracerProvider()
    with provider.get_tracer("smoke").start_as_current_span("span") as span:
        assert span.get_span_context().is_valid


def _opentelemetry_exporter() -> None:
    exporter = OTLPSpanExporter(endpoint="localhost:4317", insecure=True)
    exporter.shutdown()


@mark.parametrize(
    ("module", "wrapper", "smoke"),
    [
        ("typing_extensions", "haiway", _typing_extensions),
        ("httpx2", "haiway.httpx", _httpx2),
        ("asyncpg", "haiway.postgres", _asyncpg),
        ("pika", "haiway.rabbitmq", _pika),
        ("starlette", "haiway.starlette", _starlette),
        ("fastapi", "haiway.fastapi", _fastapi),
        ("opentelemetry.sdk", "haiway.opentelemetry", _opentelemetry_sdk),
        (
            "opentelemetry.exporter.otlp.proto.grpc",
            "haiway.opentelemetry",
            _opentelemetry_exporter,
        ),
    ],
)
def test_dependency_smoke(module: str, wrapper: str, smoke: Callable[[], None]) -> None:
    assert import_module(module) is not None
    assert import_module(wrapper) is not None
    smoke()
