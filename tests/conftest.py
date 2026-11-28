from collections.abc import Generator

import pytest

from haiway.context.identifier import ContextIdentifier
from haiway.context.state import ContextState
from haiway.context.types import ContextMissing


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
