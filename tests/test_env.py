import os
from collections.abc import Generator
from pathlib import Path

import pytest

from haiway import load_env


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
