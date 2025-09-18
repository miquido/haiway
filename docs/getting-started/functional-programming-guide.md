# From OOP to Functional Programming with Haiway

This guide is a concise map for OOP developers adopting Haiway. It focuses on key differences and
the core patterns you need day‑to‑day, aligned with Haiway’s guidelines.

## 1) Immutability First (State)

Traditional OOP encourages mutating objects. Haiway centers on immutable `State` objects and pure
transformations.

```python
from datetime import datetime
from haiway import State

class User(State):
    name: str
    email: str | None = None
    login_count: int = 0
    last_login: datetime | None = None

    def with_login(self) -> "User":
        return self.updated(
            login_count=self.login_count + 1,
            last_login=datetime.now(),
        )

u = User(name="Alice", email="alice@example.com")
u2 = u.with_login()  # u remains unchanged
```

Why it matters:

- Thread‑safe sharing by default; easy reasoning and testing
- `.updated()` copies with structural sharing for performance
- Collections: prefer `Sequence`/`Mapping`/`Set` (sequences/sets become immutable; mappings stay
  dicts)

## 2) Composition over Inheritance (Protocols)

Replace deep hierarchies with small protocol contracts and compose behaviors as data.

```python
from typing import Protocol, runtime_checkable
from haiway import State

@runtime_checkable
class Barking(Protocol):
    async def __call__(self, name: str) -> str: ...

class Dog(State):
    name: str
    bark: Barking

async def loud_bark(name: str) -> str:
    return f"{name} BARKS!"

dog = Dog(name="Rex", bark=loud_bark)
# Swap implementation by constructing a different state if needed
```

Why it matters:

- Loose coupling and easy swapping in tests/production
- Clear, minimal interfaces; no fragile base classes

## 3) Context‑Driven Access and statemethod

Haiway’s context provides scoped execution, dependency resolution by type, and observability. Access
state using its type only, and expose behavior with `@statemethod`.

```python
from typing import Protocol, runtime_checkable
from haiway import State, ctx, statemethod

@runtime_checkable
class EmailSending(Protocol):
    async def __call__(self, to: str, subject: str, body: str) -> bool: ...

class NotificationService(State):
    email_sending: EmailSending

    @statemethod
    async def notify(self, user_id: str, message: str) -> bool:
        return await self.email_sending(
            to=f"user-{user_id}@example.com",
            subject="Notification",
            body=message,
        )

async def smtp_send(to: str, subject: str, body: str) -> bool:
    # ... real send
    return True

service = NotificationService(send_email=smtp_send)
async with ctx.scope("app", service):
    # Class call resolves instance from context; instance call uses itself
    await NotificationService.notify("123", "Welcome")
```

Key rules:

- `ctx.state(T)` resolves by type only
- `@statemethod` always works on an instance: class calls resolve from context, instance calls use
  that instance
- Prefer `@statemethod` over `@classmethod` for helpers that need contextual state

### State Resolution Priority

When multiple states of the same type exist, Haiway resolves using:

1. Explicit state passed to `ctx.scope(...)` (highest)
1. Disposables (resources yielded into the scope)
1. Presets
1. Parent context (lowest)

## 4) Structured Concurrency (Scoped Tasks)

Start tasks within the active scope; Haiway manages their lifecycle and cleanup.

```python
import asyncio
from haiway import ctx

async def worker():
    # Inherits current scope (state, observability, variables)
    await asyncio.sleep(0.1)

async def main():
    async with ctx.scope("app"):
        t = ctx.spawn(worker)
        await t  # or gather/spawn more
```

Why it matters:

- Automatic cancellation/cleanup with the scope
- Consistent access to contextual state and observability

## 5) Testing the Functional Way

Tests become wiring exercises: build small states and protocols, enter a scope, and call
`@statemethod`s.

```python
from typing import Sequence
from haiway import State, ctx, statemethod

class UsersFetching(State):
    async def __call__(self) -> Sequence[str]: ...

class UsersService(State):
    fetching: UsersFetching

    @statemethod
    async def all(self) -> Sequence[str]:
        return await self.fetching()

async def fake_fetching() -> Sequence[str]:
    return ("alice", "bob")

svc = UsersService(fetching=fake_fetching)
async with ctx.scope("test", svc):
    assert await UsersService.all() == ("alice", "bob")
```

Guidelines:

- Keep business logic in pure functions; pass dependencies explicitly
- Use protocols for seams; swap implementations in tests
- Avoid hidden globals/singletons; rely on `ctx.scope(...)`

## 6) Tips

- Extract pure functions from stateful methods; return new `State` instead of mutating
- Introduce protocol boundaries for external effects (I/O, HTTP, DB)
- Wrap work in `ctx.scope(...)`; inject only what’s needed by type
- Replace `@classmethod` helpers with `@statemethod` to ensure instance‑based execution
- Use abstract collection types (`Sequence`, `Mapping`, `Set`) and modern unions (`T | None`)

## Quick Reference

- State access: `ctx.state(T)` by type only
- Priority: explicit > disposables > presets > parent context
- Methods: use `@statemethod` for helpers that may be called from class or instance
- Concurrency: use `ctx.spawn(...)` inside an active `ctx.scope(...)`
- Observability: `ctx.record(event=..., attributes={...})` or `ctx.log_info(...)`

This functional, immutable, and context‑driven approach yields predictable, testable, and
concurrent‑safe systems with minimal ceremony.
