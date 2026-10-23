import asyncio
from importlib import import_module
from importlib.metadata import version

from haiway import State, ctx

INTEGRATIONS = (
    "haiway.httpx",
    "haiway.postgres",
    "haiway.rabbitmq",
    "haiway.starlette",
    "haiway.fastapi",
    "haiway.opentelemetry",
)


class Greeting(State):
    name: str


async def run() -> None:
    async with ctx.scope("smoke", Greeting(name="haiway")):
        if ctx.state(Greeting).name != "haiway":
            raise RuntimeError("State was not available within the scope")


for module in INTEGRATIONS:
    import_module(module)

asyncio.run(run())
print(f"haiway smoke ok: {version('haiway')}")
