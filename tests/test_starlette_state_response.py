import asyncio
from collections.abc import MutableSequence, Sequence
from datetime import UTC, datetime
from json import loads
from typing import Annotated
from uuid import UUID, uuid4

import pytest

pytest.importorskip("starlette")

from pytest import mark, raises
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from haiway import Sensitive, State, ctx
from haiway.starlette import ServerContext, StateResponse, application
from tests.asgi import Result, running, send_request


class ExampleState(State):
    value: str = "example"


class Item(State):
    name: str
    count: int = 0


@mark.asyncio
async def test_state_response_writes_state_as_json() -> None:
    async def endpoint(request: Request) -> Response:
        return StateResponse(Item(name="one", count=2), status_code=201)

    app: Starlette = application(routes=[Route("/example", endpoint)])

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 201
    assert result.headers["content-type"] == "application/json"
    assert result.headers["content-length"] == str(len(result.body))
    assert loads(result.body) == {"name": "one", "count": 2}


@mark.parametrize(
    ("content", "expected"),
    (
        ((), []),
        ((Item(name="a"),), [{"name": "a", "count": 0}]),
        (
            [Item(name="a"), Item(name="b", count=1)],
            [{"name": "a", "count": 0}, {"name": "b", "count": 1}],
        ),
    ),
)
def test_state_response_writes_sequence_as_json_array(
    content: Sequence[Item],
    expected: Sequence[object],
) -> None:
    assert loads(StateResponse(content).body) == expected


def test_state_response_keeps_headers_and_media_type() -> None:
    response = StateResponse(
        Item(name="one"),
        headers={"x-example": "yes"},
        media_type="application/vnd.example+json",
    )

    assert response.headers["x-example"] == "yes"
    assert response.headers["content-type"] == "application/vnd.example+json"


def test_state_response_fails_when_building_on_unencodable_state() -> None:
    class Opaque(State):
        value: object

    with raises(ValueError):
        StateResponse(Opaque(value=object()))


class Address(State):
    city: str


class Person(State):
    identifier: UUID
    born: datetime
    tags: Sequence[str] = ()
    address: Address | None = None
    secret: Annotated[str, Sensitive()] = "hidden"


@mark.asyncio
async def test_state_response_writes_the_types_of_the_state_within_the_request_scope() -> None:
    identifier: UUID = uuid4()
    born: datetime = datetime(2000, 1, 2, 3, 4, 5, tzinfo=UTC)

    async def endpoint(request: Request) -> Response:
        return StateResponse(
            (
                Person(
                    identifier=identifier,
                    born=born,
                    tags=("a", "b"),
                    address=Address(city=ctx.state(ExampleState).value),
                ),
            )
        )

    app: Starlette = application(
        ServerContext(ExampleState(value="Warsaw")),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        result: Result = await send_request(app)

    (decoded,) = loads(result.body)
    assert Person.validate(decoded) == Person(
        identifier=identifier,
        born=born,
        tags=("a", "b"),
        address=Address(city="Warsaw"),
    )


def test_state_response_body_matches_to_json() -> None:
    person = Person(identifier=uuid4(), born=datetime(2000, 1, 1, tzinfo=UTC))

    assert StateResponse(person).body == person.to_json().encode()


def test_state_response_runs_background_task() -> None:
    ran: MutableSequence[str] = []

    async def task() -> None:
        ran.append("ran")

    async def run() -> None:
        await send_request(
            StateResponse(Item(name="one"), background=BackgroundTask(task)),
        )

    asyncio.run(run())

    assert ran == ["ran"]
