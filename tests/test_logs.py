import ast
import inspect
import json
import re
import textwrap
from collections.abc import Generator
from datetime import datetime
from logging import DEBUG, INFO, Formatter, Handler, Logger, LogRecord, StreamHandler, getLogger
from logging import root as root_logger
from typing import Any

import pytest

from haiway import JSONLogFormatter, ctx, setup_logging

# CPython renders an object without a custom repr as "<Name object at 0xADDRESS>";
# matching the whole shape keeps the check independent of paths which merely
# happen to contain "0x"
MEMORY_ADDRESS = re.compile(r"at 0x[0-9a-fA-F]+")


class _CollectingHandler(Handler):
    def __init__(
        self,
        records: list[LogRecord],
    ) -> None:
        super().__init__(level=DEBUG)
        self.records: list[LogRecord] = records

    def emit(
        self,
        record: LogRecord,
    ) -> None:
        self.records.append(record)


@pytest.fixture(autouse=True)
def restore_logging() -> Generator[None]:
    """
    Restore the logging configuration mutated by ``setup_logging``.
    """

    existing_loggers: dict[str, Any] = root_logger.manager.loggerDict.copy()
    snapshot: list[tuple[Logger, list[Any], int, bool, bool]] = [
        (
            logger,
            logger.handlers[:],
            logger.level,
            logger.propagate,
            logger.disabled,
        )
        for logger in (
            root_logger,
            *(
                existing
                for existing in root_logger.manager.loggerDict.values()
                if isinstance(existing, Logger)
            ),
        )
    ]
    try:
        yield

    finally:
        # loggers created by the test itself would otherwise outlive it, keeping
        # their handlers attached to a stream which is closed by then
        root_logger.manager.loggerDict.clear()
        root_logger.manager.loggerDict.update(existing_loggers)
        for logger, handlers, level, propagate, disabled in snapshot:
            logger.handlers[:] = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled


def console_handler(logger: Logger) -> StreamHandler[Any]:
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, StreamHandler)
    return handler


def test_custom_formatter_instance_is_shared_by_all_loggers() -> None:
    formatter = Formatter("[%(levelname)-4s] [%(name)s] %(message)s")
    setup_logging(
        "test_logs_custom_first",
        "test_logs_custom_second",
        formatter=formatter,
        disable_existing_loggers=False,
    )

    assert console_handler(root_logger).formatter is formatter
    assert console_handler(getLogger("test_logs_custom_first")).formatter is formatter
    assert console_handler(getLogger("test_logs_custom_second")).formatter is formatter


def test_custom_formatter_subclass_formats_emitted_records(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CustomFormatter(Formatter):
        def format(
            self,
            record: Any,
        ) -> str:
            return f"CUSTOM|{record.levelname}|{record.name}|{record.getMessage()}"

    setup_logging(
        "test_logs_subclass",
        formatter=CustomFormatter(),
        disable_existing_loggers=False,
    )
    getLogger("test_logs_subclass").info("emitted")

    assert capsys.readouterr().out == "CUSTOM|INFO|test_logs_subclass|emitted\n"


def test_formatter_without_timestamp_omits_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(
        "test_logs_no_time",
        formatter=Formatter("[%(levelname)-4s] [%(name)s] %(message)s"),
        disable_existing_loggers=False,
    )
    getLogger("test_logs_no_time").warning("no timestamp")

    assert capsys.readouterr().out == "[WARNING] [test_logs_no_time] no timestamp\n"


def test_debug_flag_controls_levels_with_custom_formatter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    formatter = Formatter("%(message)s")
    setup_logging(
        "test_logs_debug",
        formatter=formatter,
        debug=True,
        disable_existing_loggers=False,
    )
    logger = getLogger("test_logs_debug")

    assert logger.level == DEBUG
    assert console_handler(logger).level == DEBUG
    logger.debug("visible")
    assert capsys.readouterr().out == "visible\n"

    setup_logging(
        "test_logs_debug",
        formatter=formatter,
        debug=False,
        disable_existing_loggers=False,
    )

    assert logger.level == INFO
    assert console_handler(logger).level == INFO
    logger.debug("hidden")
    assert capsys.readouterr().out == ""


def test_time_argument_is_no_longer_accepted() -> None:
    with pytest.raises(TypeError):
        setup_logging("test_logs_time", time=False)  # pyright: ignore[reportCallIssue]


def test_json_formatter_renders_all_record_attributes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(
        "test_logs_json",
        formatter=JSONLogFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z"),
        disable_existing_loggers=False,
    )
    getLogger("test_logs_json").info(
        "processed %d items",
        42,
        extra={"request_id": "req-7", "tenant": "acme"},
    )

    output = capsys.readouterr().out
    assert output.endswith("\n")
    payload = json.loads(output)

    # rendered fields
    assert payload["message"] == "processed 42 items"
    assert datetime.strptime(payload["time"], "%Y-%m-%dT%H:%M:%S%z").tzinfo is not None

    # record attributes, under their original names
    assert payload["name"] == "test_logs_json"
    assert payload["msg"] == "processed %d items"
    assert payload["args"] == [42]
    assert payload["levelname"] == "INFO"
    assert payload["levelno"] == INFO
    assert payload["module"] == "test_logs"
    assert payload["funcName"] == "test_json_formatter_renders_all_record_attributes"
    assert isinstance(payload["lineno"], int)

    # caller provided fields, unfiltered
    assert payload["request_id"] == "req-7"
    assert payload["tenant"] == "acme"

    # noise, omitted while holding None
    assert "exc_info" not in payload
    assert "exc_text" not in payload
    assert "stack_info" not in payload
    assert "taskName" not in payload  # no running asyncio task


def test_json_formatter_keeps_every_record_attribute_holding_a_value() -> None:
    record = getLogger("test_logs_json_all").makeRecord(
        "test_logs_json_all",
        INFO,
        "test.py",
        1,
        "message",
        None,
        None,
        extra={"request_id": "req-7"},
    )
    payload = json.loads(JSONLogFormatter().format(record))
    attributes = record.__dict__

    assert {key for key, value in attributes.items() if value is not None} <= payload.keys()
    assert {key for key, value in attributes.items() if value is None}.isdisjoint(payload.keys())
    assert payload.keys() - attributes.keys() == {"time", "message"}


def test_json_formatter_omits_fields_holding_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(
        "test_logs_json_none",
        formatter=JSONLogFormatter(),
        disable_existing_loggers=False,
    )
    getLogger("test_logs_json_none").info(
        "sparse",
        extra={"present": "value", "absent": None},
    )

    payload = json.loads(capsys.readouterr().out)

    assert payload["present"] == "value"
    assert "absent" not in payload


def test_json_formatter_renders_exception_info_as_formatted_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(
        "test_logs_json_error",
        formatter=JSONLogFormatter(),
        disable_existing_loggers=False,
    )
    try:
        raise ValueError("broken")

    except ValueError:
        getLogger("test_logs_json_error").exception("failed")

    payload = json.loads(capsys.readouterr().out)

    assert payload["levelname"] == "ERROR"
    assert payload["message"] == "failed"
    assert payload["exc_info"].startswith("Traceback (most recent call last):")
    assert "raise ValueError" in payload["exc_info"]
    assert payload["exc_info"].endswith("ValueError: broken")
    assert MEMORY_ADDRESS.search(payload["exc_info"]) is None  # no memory addresses


def test_json_formatter_keeps_stack_info(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(
        "test_logs_json_stack",
        formatter=JSONLogFormatter(),
        disable_existing_loggers=False,
    )
    getLogger("test_logs_json_stack").info("with stack", stack_info=True)

    payload = json.loads(capsys.readouterr().out)

    assert "Stack (most recent call last)" in payload["stack_info"]
    assert "test_json_formatter_keeps_stack_info" in payload["stack_info"]


def test_json_formatter_default_time_is_iso_with_milliseconds_and_offset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(
        "test_logs_json_time",
        formatter=JSONLogFormatter(),
        disable_existing_loggers=False,
    )
    getLogger("test_logs_json_time").info("timed")

    payload = json.loads(capsys.readouterr().out)
    parsed = datetime.fromisoformat(payload["time"])

    assert parsed.tzinfo is not None
    assert parsed.microsecond % 1000 == 0  # milliseconds precision
    assert (datetime.now(tz=parsed.tzinfo) - parsed).total_seconds() < 10


class Described:
    def __str__(self) -> str:
        return "described-value"


class Opaque:
    pass


def test_json_formatter_resolves_unserializable_values_to_readable_strings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    try:
        raise ValueError("broken")

    except ValueError as exc:
        traceback = exc.__traceback__

    setup_logging(
        "test_logs_json_values",
        formatter=JSONLogFormatter(),
        disable_existing_loggers=False,
    )
    getLogger("test_logs_json_values").info(
        "values",
        extra={
            "described": Described(),
            "opaque": Opaque(),
            "nested": {"inner": Opaque()},
            "error": ValueError("broken"),
            "kind": ValueError,
            "traceback": traceback,
        },
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert captured.err == ""  # no handler error
    assert payload["described"] == "described-value"
    assert payload["opaque"] == "<Opaque>"  # default repr address stripped
    assert payload["nested"] == {"inner": "<Opaque>"}
    assert payload["error"] == "ValueError: broken"
    assert payload["kind"] == "ValueError"
    assert "raise ValueError" in payload["traceback"]
    assert MEMORY_ADDRESS.search(captured.out) is None  # no memory addresses


def test_json_formatter_output_is_single_line_per_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_logging(
        "test_logs_json_lines",
        formatter=JSONLogFormatter(),
        disable_existing_loggers=False,
    )
    logger = getLogger("test_logs_json_lines")
    logger.info("first\nstill first")
    try:
        raise ValueError("multi\nline")

    except ValueError:
        logger.exception("second")

    lines = capsys.readouterr().out.splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["message"] == "first\nstill first"
    assert json.loads(lines[1])["exc_info"].count("\n") > 1


def test_json_formatter_renders_records_rendered_by_another_formatter() -> None:
    # `Formatter.format` leaves `message` - and `asctime` when a date format is
    # used - on the shared record, which every later handler sees as a field
    record = getLogger("test_logs_json_shared").makeRecord(
        "test_logs_json_shared",
        INFO,
        "test.py",
        1,
        "shared %s",
        ("record",),
        None,
    )
    Formatter("%(asctime)s %(message)s").format(record)

    payload = json.loads(JSONLogFormatter().format(record))

    assert payload["message"] == "shared record"
    assert "asctime" not in payload  # the output stays independent of handler order


@pytest.mark.skipif(not __debug__, reason="assertions are stripped in optimized builds")
def test_json_formatter_asserts_on_conflicting_fields_in_debug_builds() -> None:
    logger = getLogger("test_logs_json_conflict")

    with pytest.raises(AssertionError) as error:
        JSONLogFormatter().format(
            logger.makeRecord(
                "test_logs_json_conflict",
                INFO,
                "test.py",
                1,
                "conflicting",
                None,
                None,
                extra={"time": "MINE", "other": "kept"},
            )
        )

    assert "conflict with rendered fields" in str(error.value)
    assert "'time'" in str(error.value)
    assert "'other'" not in str(error.value)  # conflicts only, not all extras


CONFLICT_SCRIPT = """
from logging import INFO, getLogger

from haiway import JSONLogFormatter

record = getLogger("conflict").makeRecord(
    "conflict",
    INFO,
    "test.py",
    1,
    "conflicting",
    None,
    None,
    extra={"time": "MINE"},
)
try:
    JSONLogFormatter().format(record)

except AssertionError:
    print("raised")

else:
    print("formatted")
"""


def test_json_formatter_conflict_check_is_a_bare_assertion() -> None:
    # a bare assert is what makes the check disappear under -O, unlike a raise
    formatting = ast.parse(textwrap.dedent(inspect.getsource(JSONLogFormatter.format)))

    assert any(isinstance(node, ast.Assert) for node in ast.walk(formatting))


def test_json_formatter_rejects_extra_colliding_with_record_attributes() -> None:
    setup_logging(
        "test_logs_json_extra",
        formatter=JSONLogFormatter(),
        disable_existing_loggers=False,
    )

    # guarded by logging itself, before the formatter is reached
    with pytest.raises(KeyError):
        getLogger("test_logs_json_extra").info("rejected", extra={"module": "mine"})


@pytest.mark.asyncio
async def test_context_logging_cannot_forge_additional_records() -> None:
    logger = getLogger("log-injection")
    records: list[LogRecord] = []
    handler = _CollectingHandler(records)
    logger.addHandler(handler)
    logger.setLevel(INFO)
    try:
        async with ctx.scope("injection", observability=logger):
            ctx.log_info("login attempt for alice\n2026-08-20 [ERROR] AUDIT: root logged in")

    finally:
        logger.removeHandler(handler)

    messages = [record.getMessage() for record in records]

    assert any("login attempt for alice" in message for message in messages)
    assert all("\n" not in message for message in messages)


@pytest.mark.asyncio
async def test_context_logging_keeps_records_with_mismatched_format() -> None:
    logger = getLogger("log-format-mismatch")
    records: list[LogRecord] = []
    handler = _CollectingHandler(records)
    logger.addHandler(handler)
    logger.setLevel(INFO)
    try:
        async with ctx.scope("mismatch", observability=logger):
            ctx.log_info("user said: %s and %d", "x")

    finally:
        logger.removeHandler(handler)

    # the record survives, including the arguments which could not be interpolated
    assert any("user said: %s and %d" in record.getMessage() for record in records)
    assert all(record.args is None or not record.args for record in records)
