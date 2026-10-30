import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.mark.skipif(shutil.which("make") is None, reason="make is required")
@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX shell and PATH")
def test_uv_check_fails_when_path_uv_stays_outdated(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _script(bin_dir / "uv", 'if [ "$1" = "self" ]; then exit 2; fi; echo "uv 0.12.0"')
    _script(bin_dir / "curl", "echo 'exit 0'")

    result = subprocess.run(
        ["make", "-f", str(ROOT / "Makefile"), "uv_check"],
        cwd=tmp_path,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "still first on PATH" in result.stdout
    assert os.fspath(tmp_path / ".local/bin") in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is required")
@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX shell and PATH")
@pytest.mark.parametrize("target", ["setup", "venv"])
def test_setup_uses_uv_installed_outside_path(tmp_path: Path, target: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installer = (
        'mkdir -p "$HOME/.local/bin"; '
        "printf '#!/bin/sh\\n"
        'if [ "$1" = "--version" ]; then echo "uv 0.13.5"; exit 0; fi\\n'
        'echo "$@" >> "$HOME/uv.log"\\n\' > "$HOME/.local/bin/uv"; '
        'chmod +x "$HOME/.local/bin/uv"'
    )
    _script(bin_dir / "curl", f"cat <<'INSTALLER'\n{installer}\nINSTALLER")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/.env.example").write_text("")
    (tmp_path / "config/pre-push").write_text("")

    result = subprocess.run(
        ["make", "-f", str(ROOT / "Makefile"), target],
        cwd=tmp_path,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "sync --all-groups --all-extras --frozen" in (tmp_path / "uv.log").read_text()
