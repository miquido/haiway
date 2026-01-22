import builtins
import importlib
import sys

import pytest


@pytest.mark.parametrize(
    ("haiway_module", "dependency", "extra_name"),
    [
        ("haiway.httpx", "httpx", "httpx"),
        ("haiway.opentelemetry", "opentelemetry", "opentelemetry"),
        ("haiway.postgres", "asyncpg", "postgres"),
        ("haiway.rabbitmq", "pika", "rabbitmq"),
    ],
)
def test_optional_extra_guard_message(
    monkeypatch,
    haiway_module: str,
    dependency: str,
    extra_name: str,
) -> None:
    # Ensure modules are re-imported
    monkeypatch.delitem(sys.modules, haiway_module, raising=False)
    if haiway_module == "haiway.httpx":
        monkeypatch.delitem(sys.modules, "haiway.httpx.client", raising=False)
    monkeypatch.delitem(sys.modules, dependency, raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == dependency:
            raise ImportError(f"{dependency} missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match=rf"haiway\[{extra_name}\]"):
        importlib.import_module(haiway_module)
