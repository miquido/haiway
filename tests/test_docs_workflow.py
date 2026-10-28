from pathlib import Path

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "docs.yml"


def test_docs_build_does_not_resync_environment() -> None:
    # pyproject sets default-groups = "all", so a syncing `uv run` would
    # install the dev group into the docs-only environment
    build = next(line for line in WORKFLOW.read_text().splitlines() if "mkdocs build" in line)
    assert "--no-sync" in build
