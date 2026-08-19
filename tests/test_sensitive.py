import json
from collections.abc import Mapping, Sequence
from logging import DEBUG, Logger, LogRecord, getLogger
from typing import Annotated, Any

import pytest

from haiway import LoggerObservability, Sensitive, State, ctx
from haiway.utils.formatting import format_str


class Credentials(State):
    user: str
    api_key: Annotated[str, Sensitive()]
    refresh_token: Annotated[str | None, Sensitive(redaction="<token>")] = None


class Service(State):
    name: str
    credentials: Credentials


type RecursiveSecrets = Annotated[str, Sensitive()] | Sequence[RecursiveSecrets]


def test_sensitive_attributes_are_not_rendered_in_str() -> None:
    rendered = str(Credentials(user="alice", api_key="sk-live-SECRET", refresh_token="rt-SECRET"))

    assert "sk-live-SECRET" not in rendered
    assert "rt-SECRET" not in rendered
    assert rendered == "Credentials(user: alice, api_key: <redacted>, refresh_token: <token>)"


def test_sensitive_attributes_are_not_rendered_in_repr() -> None:
    rendered = repr(Credentials(user="alice", api_key="sk-live-SECRET"))

    assert "sk-live-SECRET" not in rendered
    assert "api_key: <redacted>" in rendered


def test_sensitive_attributes_are_not_rendered_within_nested_state() -> None:
    rendered = str(
        Service(
            name="billing",
            credentials=Credentials(user="alice", api_key="sk-live-SECRET"),
        )
    )

    assert "sk-live-SECRET" not in rendered


def test_sensitive_attributes_are_not_rendered_by_format_str() -> None:
    rendered = format_str({"service": Credentials(user="alice", api_key="sk-live-SECRET")})

    assert "sk-live-SECRET" not in rendered
    assert "<redacted>" in rendered


def test_sensitive_attributes_keep_their_value() -> None:
    credentials = Credentials(user="alice", api_key="sk-live-SECRET")

    assert credentials.api_key == "sk-live-SECRET"
    assert credentials.to_mapping()["api_key"] == "sk-live-SECRET"
    assert json.loads(credentials.to_json())["api_key"] == "sk-live-SECRET"
    assert Credentials.from_json(credentials.to_json()) == credentials


def test_sensitive_attributes_keep_validation_and_schema() -> None:
    with pytest.raises(Exception):  # noqa: B017 - any validation failure is fine
        Credentials(user="alice", api_key=42)  # pyright: ignore[reportArgumentType]

    schema: Any = json.loads(Credentials.json_schema())

    assert schema["properties"]["api_key"] == {"type": "string"}
    assert schema["required"] == ["user", "api_key"]


def _redactions(
    state: type[State],
    /,
) -> Mapping[str, str]:
    return {
        field.name: field.redaction for field in state.__FIELDS__ if field.redaction is not None
    }


def test_sensitivity_survives_optional_attributes() -> None:
    class Optionals(State):
        secret: Annotated[str, Sensitive()] = "default-SECRET"

    assert "default-SECRET" not in str(Optionals())


def test_sensitivity_survives_union_annotations() -> None:
    class Unions(State):
        secret: Annotated[str, Sensitive()] | None = None

    assert _redactions(Unions) == {"secret": "<redacted>"}
    assert "union-SECRET" not in str(Unions(secret="union-SECRET"))


def test_sensitivity_survives_container_annotations() -> None:
    class Containers(State):
        secrets: Sequence[Annotated[str, Sensitive()]] = ()
        mapped: Mapping[str, Annotated[str, Sensitive(redaction="<token>")]] = {}

    containers = Containers(secrets=("seq-SECRET",), mapped={"key": "map-SECRET"})

    assert _redactions(Containers) == {"secrets": "<redacted>", "mapped": "<token>"}
    assert "seq-SECRET" not in str(containers)
    assert "map-SECRET" not in str(containers)
    # the whole container is replaced - the sensitive part of it is not known here
    assert "secrets: <redacted>" in str(containers)


def test_sensitivity_survives_recursive_aliases() -> None:
    class Recursive(State):
        payload: RecursiveSecrets = ""

    assert _redactions(Recursive) == {"payload": "<redacted>"}
    assert "alias-SECRET" not in str(Recursive(payload=("alias-SECRET",)))


def test_sensitive_marker_class_redacts_like_an_instance() -> None:
    # `Sensitive` needs no argument, so writing the class is as reasonable as
    # writing an instance of it - and leaving it unmarked would render in full
    # the very attribute the marker was put there to withhold
    class Marked(State):
        user: str
        api_key: Annotated[str, Sensitive]

    assert _redactions(Marked) == {"api_key": "<redacted>"}

    rendered = str(Marked(user="alice", api_key="sk-live-SECRET"))
    assert "sk-live-SECRET" not in rendered
    assert rendered == "Marked(user: alice, api_key: <redacted>)"

    # the value itself is kept, the same way an instance marker keeps it
    assert Marked(user="alice", api_key="sk-live-SECRET").api_key == "sk-live-SECRET"


def test_sensitive_marker_class_survives_nested_annotations() -> None:
    class Nested(State):
        optional: Annotated[str | None, Sensitive] = None
        listed: Sequence[Annotated[str, Sensitive]] = ()
        mapped: Mapping[str, Annotated[str, Sensitive]] = {}

    assert _redactions(Nested) == {
        "optional": "<redacted>",
        "listed": "<redacted>",
        "mapped": "<redacted>",
    }

    rendered = str(Nested(optional="a-SECRET", listed=("b-SECRET",), mapped={"k": "c-SECRET"}))
    assert "SECRET" not in rendered


def test_sensitive_marker_instance_keeps_its_own_redaction() -> None:
    # an instance is not replaced by one carrying the default redaction
    class Marked(State):
        api_key: Annotated[str, Sensitive(redaction="<api-key>")]

    assert _redactions(Marked) == {"api_key": "<api-key>"}


def test_sensitive_rejects_empty_redaction() -> None:
    # validated with a raise instead of an assertion - an optimized build would
    # strip the check and render nothing in place of the empty redaction
    with pytest.raises(ValueError):
        Sensitive(redaction="")


@pytest.mark.asyncio
async def test_recorded_attributes_of_sensitive_state_are_not_logged() -> None:
    logger: Logger = getLogger("sensitive-observability")
    records: list[LogRecord] = []

    class Collecting:
        def handle(self, record: LogRecord) -> None:
            records.append(record)

        level = DEBUG

        def acquire(self) -> None: ...
        def release(self) -> None: ...

    handler = Collecting()
    previous_level: int = logger.level
    logger.addHandler(handler)  # pyright: ignore[reportArgumentType]
    logger.setLevel(DEBUG)
    try:
        async with ctx.scope(
            "sensitive",
            observability=LoggerObservability(logger),
        ):
            ctx.log_info(f"using {Credentials(user='alice', api_key='sk-live-SECRET')}")

    finally:
        # remove only what this test added and put the level back - loggers are
        # process wide, and clearing handlers would drop anyone else's
        logger.removeHandler(handler)  # pyright: ignore[reportArgumentType]
        logger.setLevel(previous_level)

    assert records
    assert all("sk-live-SECRET" not in record.getMessage() for record in records)
