from collections.abc import Generator

import pytest

from haiway import ctx
from haiway.context.identifier import ContextIdentifier
from haiway.context.state import ContextState
from haiway.context.types import ContextMissing


@pytest.fixture(autouse=True)
def cleanup_background_tasks() -> Generator[None]:
    """
    Drop references to background tasks registered by the finished test.

    Task cancellation itself is provided by pytest-asyncio, which builds a fresh
    event loop per test and cancels everything still pending when it closes that
    loop. This only clears the registry so it does not accumulate dead loops for
    the whole session - and so that widening ``asyncio_default_fixture_loop_scope``
    past ``function`` cannot silently carry tasks into the next test.
    """
    yield
    ctx.shutdown_background_tasks()


@pytest.fixture(autouse=True)
def verify_context_not_leaked() -> Generator[None]:
    """
    Fail the test that leaks context state instead of the one that trips over it.

    Sync tests share the main thread's context and ``asyncio.Runner`` copies it,
    so a single unbalanced scope entry contaminates every later test in the
    session. Without this guard the failure surfaces somewhere unrelated, as an
    unexpectedly non-empty state or a scope that refuses to be missing.
    """
    yield
    assert ContextState.snapshot() == (), "test leaked context state"

    try:
        leaked = ContextIdentifier.current()

    except ContextMissing:
        return

    raise AssertionError(f"test leaked a context scope: {leaked.name}")
