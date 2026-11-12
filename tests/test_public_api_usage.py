import importlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import haiway
from haiway import helpers

_SOURCES: Path = Path(haiway.__file__).parent

# a private name of the standard library, reached through its module or imported from it
_PRIVATE_PYTHON: re.Pattern[str] = re.compile(
    r"\b(?:annotationlib|asyncio|builtins|contextvars|functools|inspect|sys|types|typing"
    r"|typing_extensions|weakref)\._[A-Za-z]\w*"
    r"|^\s*from (?:annotationlib|asyncio|functools|inspect|types|typing|typing_extensions)"
    r" import (?:\([^)]*|[^(\n]*)\b_[A-Za-z]",
    re.MULTILINE,
)
# a private function, macro or type of CPython - free to change between its releases
_PRIVATE_C: re.Pattern[str] = re.compile(r"\b_Py\w+|\bPyUnstable_\w+")


def _sources(*suffixes: str) -> Iterator[Path]:
    for suffix in suffixes:
        yield from sorted(_SOURCES.rglob(f"*{suffix}"))


@pytest.mark.parametrize(
    "path",
    list(_sources(".py")),
    ids=lambda path: str(path.relative_to(_SOURCES)),
)
def test_python_sources_use_no_private_standard_library_api(path: Path) -> None:
    assert _PRIVATE_PYTHON.findall(path.read_text()) == []


@pytest.mark.parametrize(
    "path",
    list(_sources(".c", ".h")),
    ids=lambda path: str(path.relative_to(_SOURCES)),
)
def test_native_sources_use_no_private_cpython_api(path: Path) -> None:
    assert _PRIVATE_C.findall(path.read_text()) == []


@pytest.mark.parametrize(
    "module",
    [
        "haiway",
        "haiway.attributes",
        "haiway.context",
        "haiway.helpers",
        "haiway.helpers.caching",
        "haiway.helpers.configuration",
        "haiway.helpers.files",
        "haiway.helpers.message_queue",
        "haiway.types",
        "haiway.utils",
    ],
)
def test_exported_names_resolve(module: str) -> None:
    exported: Any = importlib.import_module(module)
    assert [name for name in exported.__all__ if not hasattr(exported, name)] == []


def test_protocols_of_public_signatures_are_exported() -> None:
    assert {
        "CacheClear",
        "ConfigurationDefining",
        "ConfigurationListing",
        "ConfigurationLoading",
        "ConfigurationRemoving",
        "FileAccess",
        "FileAccessing",
        "FileReading",
        "FileWriting",
        "MQMessageAcknowledging",
        "MQMessageRejecting",
        "PathTraversing",
    } <= set(helpers.__all__)


@pytest.mark.parametrize("method", ["updating", "validate", "__replace__", "__deepcopy__"])
def test_native_state_methods_are_documented(method: str) -> None:
    documentation: str | None = getattr(haiway.State, method).__doc__
    assert documentation
    assert not documentation.startswith(method)  # the signature line is read off, not shown


@pytest.mark.parametrize(
    "source",
    [
        "x = typing._GenericAlias",
        "from functools import _make_key",
        "from typing import Any, _GenericAlias",
        "from functools import (\n    update_wrapper,\n    _make_key,\n)",
    ],
)
def test_private_usage_is_recognized(source: str) -> None:
    assert _PRIVATE_PYTHON.findall(source)


def test_public_usage_is_not_flagged() -> None:
    source: str = "from typing import Any, final\nvalue = typing.get_origin(x)"
    assert not _PRIVATE_PYTHON.findall(source)
