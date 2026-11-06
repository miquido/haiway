import os
from collections.abc import Generator
from pathlib import Path

import pytest

from haiway import (
    getenv,
    getenv_base64,
    getenv_bool,
    getenv_float,
    getenv_int,
    getenv_str,
    load_env,
    parse_bool,
)


@pytest.fixture
def clean_environ() -> Generator[None]:
    snapshot = dict(os.environ)
    try:
        yield

    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def test_load_env_strips_keys(
    tmp_path: Path,
    clean_environ: None,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "A=1",
                "  B=2",
                "export C=3",
                "D = 4",
                "\tE\t=\t5",
            )
        )
    )

    load_env(str(env_file))

    # every loaded key has to be reachable through the regular lookup
    assert os.getenv("A") == "1"
    assert os.getenv("B") == "2"
    assert os.getenv("C") == "3"
    assert os.getenv("D") == "4"
    assert os.getenv("E") == "5"


def test_load_env_ignores_invalid_lines(
    tmp_path: Path,
    clean_environ: None,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "# COMMENTED=commented",
                "   # INDENTED=indented",
                "NO_ASSIGNMENT",
                "EMPTY=",
                "BLANK=   ",
                "   =orphan",
                "",
                "VALID=value",
            )
        )
    )

    load_env(str(env_file))

    assert "COMMENTED" not in os.environ
    assert "INDENTED" not in os.environ
    assert "NO_ASSIGNMENT" not in os.environ
    assert "EMPTY" not in os.environ
    assert "BLANK" not in os.environ
    assert os.getenv("VALID") == "value"


def test_load_env_keeps_value_separators(
    tmp_path: Path,
    clean_environ: None,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("URL=postgres://user:pass@host:5432/db?opt=1\n")

    load_env(str(env_file))

    assert os.getenv("URL") == "postgres://user:pass@host:5432/db?opt=1"


def test_load_env_override(
    tmp_path: Path,
    clean_environ: None,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("  KEY=from_file\n")
    os.environ["KEY"] = "preexisting"

    load_env(str(env_file))
    assert os.getenv("KEY") == "preexisting"

    load_env(str(env_file), override=True)
    assert os.getenv("KEY") == "from_file"


def test_load_env_missing_file(
    tmp_path: Path,
    clean_environ: None,
) -> None:
    load_env(str(tmp_path / "absent.env"))  # has to not raise


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "t", "T"])
def test_parse_bool_truthy(raw: str) -> None:
    assert parse_bool(raw) is True


@pytest.mark.parametrize("raw", ["false", "0", "f", "", "yes", "on", "tru", " true"])
def test_parse_bool_everything_else_is_false(raw: str) -> None:
    assert parse_bool(raw) is False


def test_getenv_maps_value_and_handles_missing(clean_environ: None) -> None:
    os.environ["MAPPED"] = "a,b"
    os.environ.pop("ABSENT", None)

    assert getenv("MAPPED", lambda value: value.split(",")) == ["a", "b"]
    assert getenv("ABSENT", str) is None
    assert getenv("ABSENT", str, default="fallback") == "fallback"


def test_getenv_required(clean_environ: None) -> None:
    os.environ.pop("ABSENT", None)

    with pytest.raises(ValueError, match="ABSENT"):
        getenv("ABSENT", str, required=True)


def test_getenv_default_ignored_when_set_and_mapping_error_wrapped(
    clean_environ: None,
) -> None:
    os.environ["NUMBER"] = "abc"

    with pytest.raises(ValueError, match="NUMBER") as info:
        getenv("NUMBER", int, default=1)

    assert isinstance(info.value.__cause__, ValueError)

    os.environ["NUMBER"] = "5"
    assert getenv("NUMBER", int, default=1) == 5


def test_getenv_bool(clean_environ: None) -> None:
    os.environ.pop("FLAG", None)
    assert getenv_bool("FLAG") is None
    assert getenv_bool("FLAG", True) is True
    assert getenv_bool("FLAG", False) is False

    os.environ["FLAG"] = "T"
    assert getenv_bool("FLAG") is True

    os.environ["FLAG"] = "nope"
    assert getenv_bool("FLAG", True) is False

    os.environ.pop("FLAG")
    with pytest.raises(ValueError, match="FLAG"):
        getenv_bool("FLAG", required=True)


def test_getenv_int(clean_environ: None) -> None:
    os.environ.pop("COUNT", None)
    assert getenv_int("COUNT") is None
    assert getenv_int("COUNT", 7) == 7

    os.environ["COUNT"] = "-42"
    assert getenv_int("COUNT") == -42

    os.environ["COUNT"] = "1.5"
    with pytest.raises(ValueError, match="COUNT"):
        getenv_int("COUNT")

    os.environ.pop("COUNT")
    with pytest.raises(ValueError, match="COUNT"):
        getenv_int("COUNT", required=True)


def test_getenv_float(clean_environ: None) -> None:
    os.environ.pop("RATIO", None)
    assert getenv_float("RATIO") is None
    assert getenv_float("RATIO", 0.5) == 0.5

    os.environ["RATIO"] = "1.25"
    assert getenv_float("RATIO") == 1.25

    os.environ["RATIO"] = "x"
    with pytest.raises(ValueError, match="RATIO"):
        getenv_float("RATIO")

    os.environ.pop("RATIO")
    with pytest.raises(ValueError, match="RATIO"):
        getenv_float("RATIO", required=True)


def test_getenv_str(clean_environ: None) -> None:
    os.environ.pop("NAME", None)
    assert getenv_str("NAME") is None
    assert getenv_str("NAME", "dflt") == "dflt"

    os.environ["NAME"] = "value"
    assert getenv_str("NAME", "dflt") == "value"

    os.environ["NAME"] = ""
    assert getenv_str("NAME", "dflt") == ""

    os.environ.pop("NAME")
    with pytest.raises(ValueError, match="NAME"):
        getenv_str("NAME", required=True)


def test_getenv_base64(clean_environ: None) -> None:
    os.environ.pop("SECRET", None)
    assert getenv_base64("SECRET", decoder=bytes.decode) is None
    assert getenv_base64("SECRET", "dflt", decoder=bytes.decode) == "dflt"

    os.environ["SECRET"] = "aGVsbG8="
    assert getenv_base64("SECRET", decoder=bytes.decode) == "hello"

    os.environ["SECRET"] = "not base64!"
    with pytest.raises(ValueError, match="not valid base64"):
        getenv_base64("SECRET", decoder=bytes.decode)

    os.environ["SECRET"] = "/w=="  # valid base64, invalid utf-8
    with pytest.raises(ValueError, match="after base64 transform"):
        getenv_base64("SECRET", decoder=bytes.decode)

    os.environ.pop("SECRET")
    with pytest.raises(ValueError, match="SECRET"):
        getenv_base64("SECRET", decoder=bytes.decode, required=True)
