# State Methods

Define helpers on `State` classes that always operate on an instance.

- Instance call: uses that instance directly
- Class call: resolves an instance from context via `ctx.state(T)`

This removes pitfalls of using `@classmethod`, where calls on instances still receive the class.

## Basic Usage

```python
from haiway import State, ctx, statemethod
from typing import Callable

class Example(State):
    do: Callable[[], str]

    @statemethod
    def do_stuff(self) -> str:
        return self.do()

# Instance call: uses that instance
inst = Example(do=lambda: "from-instance")
assert inst.do_stuff() == "from-instance"

# Class call: resolves from current ctx
async def run() -> None:
    async with ctx.scope("ex", Example(do=lambda: "from-ctx")):
        assert Example.do_stuff() == "from-ctx"
```

## Comparison with `@classmethod`

```python
class UsingClassmethod(State):
    do: Callable[[], str]

    @classmethod
    def do_stuff(cls) -> str:
        # cls is the class even when called via an instance
        return ctx.state(cls).do()

inst = UsingClassmethod(do=lambda: "from-instance")

# This will NOT use `inst` — it still resolves via ctx.state(UsingClassmethod)
inst.do_stuff()
```

Use `@statemethod` when you want class and instance calls to share a single, instance-based helper.

## Best Practices

- Prefer plain instance methods when you never need class-level calls
- Use for service-like `State` classes that expose helpers through both class and instance
- Keep helpers focused; inject dependencies via `State` attributes
