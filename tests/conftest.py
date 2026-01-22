from collections.abc import Generator

import pytest

from haiway import ctx


@pytest.fixture(autouse=True)
def cleanup_background_tasks() -> Generator[None]:
    """
    Ensure no background tasks or cached annotations leak between tests.
    """
    yield
    try:
        ctx.shutdown_background_tasks()
    except RuntimeError:
        # No running loop; nothing to cancel.
        pass


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    # Allow potential anyio-using tests to run with asyncio backend if added.
    return "asyncio"
