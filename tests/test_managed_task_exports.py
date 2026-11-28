from importlib import import_module

from pytest import mark

import haiway
import haiway.context

INTERFACE_NAMES = (
    "AttemptCancelled",
    "AttemptCompleted",
    "AttemptEnd",
    "AttemptFailed",
    "AttemptFailure",
    "AttemptRecording",
    "AttemptRetried",
    "CancelRequesting",
    "ChangeWaiting",
    "ClaimedAttempt",
    "ClaimedInterrupted",
    "ClaimedTask",
    "InMemoryTaskStore",
    "LeaseRenewal",
    "LeaseRenewing",
    "LeaseState",
    "LeaseTimings",
    "LeasedCoordination",
    "LocalCoordination",
    "ManagedTask",
    "ManagedTasks",
    "NewTask",
    "ObservabilityLink",
    "ObservabilityScopeEntering",
    "RegisteredTask",
    "RegisteredTaskCall",
    "RegisteredTaskSpawning",
    "StoreCoordination",
    "StoredAttempt",
    "StoredCancelled",
    "StoredCompleted",
    "StoredFailed",
    "StoredFinished",
    "StoredQueued",
    "StoredRetryPending",
    "StoredRunning",
    "StoredStatus",
    "StoredTask",
    "TaskAvailability",
    "TaskCancellation",
    "TaskCancelled",
    "TaskClaiming",
    "TaskCompleted",
    "TaskFailed",
    "TaskFailure",
    "TaskFailureRecord",
    "TaskFilter",
    "TaskInserting",
    "TaskInterrupted",
    "TaskKeyset",
    "TaskListing",
    "TaskLookup",
    "TaskOutcome",
    "TaskQuerying",
    "TaskReading",
    "TaskReleasing",
    "TaskRetryPolicy",
    "TaskRoot",
    "TaskRuntime",
    "TaskStatus",
    "TaskStatusFilter",
    "TaskStore",
    "TaskStoreSession",
    "TaskSummary",
    "TaskSummaryFinished",
    "TaskSummaryUnfinished",
    "TaskTimeBefore",
    "TaskTimeBetween",
    "TaskTimeFrom",
    "TaskTimeLimitExceeded",
    "TaskTimeRange",
    "TaskUnsupported",
    "TasksFinished",
    "TasksUnfinished",
    "UnsupportedFailing",
    "ctx",
    "managed_task",
)


@mark.parametrize("name", INTERFACE_NAMES)
def test_interface_name_is_exported_from_haiway(name: str) -> None:
    assert name in haiway.__all__
    assert getattr(haiway, name) is not None


@mark.parametrize("name", INTERFACE_NAMES)
def test_interface_name_is_exported_from_context(name: str) -> None:
    assert name in haiway.context.__all__
    assert getattr(haiway.context, name) is getattr(haiway, name)


def test_postgres_task_store_is_exported_from_postgres() -> None:
    assert hasattr(import_module("haiway.postgres"), "PostgresTaskStore")
