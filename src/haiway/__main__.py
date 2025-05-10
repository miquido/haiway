from asyncio import run
from collections.abc import Mapping, Sequence
from logging import getLogger

# from haiway.metrics.telemetry import OpenTelemetryMetrics
from haiway import State, ctx
from haiway.helpers.observability import LoggerObservability
from haiway.utils.logs import setup_logging


class Example(State):
    value: str
    more: int
    sequence: Sequence[str]
    mapping: Mapping[str, str]


setup_logging(
    "root",
)


async def main():
    async with ctx.scope(
        "root",
        observability=LoggerObservability(
            getLogger("root"),
            summarize_context=True,
        ),
        # metrics=OpenTelemetryMetrics.handler(
        #     service="test",
        # ),
    ):
        ctx.event(
            Example(
                value="ex",
                more=3,
                sequence=[],
                mapping={},
            )
        )
        ctx.event(
            Example(
                value="exam",
                more=5,
                sequence=[],
                mapping={},
            )
        )
        ctx.metric("example", value=42)
        ctx.log_info("what?")
        async with ctx.scope("nested"):
            ctx.event(
                Example(
                    value="exnes",
                    more=42,
                    sequence=[],
                    mapping={},
                )
            )
            ctx.metric("example", value=84)
            ctx.log_info("nested?")
            async with ctx.scope("nested-more"):
                ctx.event(
                    Example(
                        value="exnesmore",
                        more=1,
                        sequence=[],
                        mapping={},
                    )
                )
                ctx.log_info("more?")

            async with ctx.scope("nested-more"):
                ctx.event(
                    Example(
                        value="exnes\nmo\nre2",
                        more=1,
                        sequence=["a", "b", "c"],
                        mapping={"a": "1", "b": "2", "c": "3"},
                    )
                )
                ctx.log_info("more2?")


run(main())
