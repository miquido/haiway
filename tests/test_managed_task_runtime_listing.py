import asyncio
import threading
from datetime import UTC, datetime
from uuid import UUID

from pytest import MonkeyPatch, mark, raises

from haiway import Pagination, ctx
from haiway.context import ContextStateMissing
from haiway.context.managed import (
    InMemoryTaskStore,
    ManagedTask,
    ManagedTasks,
    NewTask,
    TaskFilter,
    TaskKeyset,
    TaskRetryPolicy,
    TaskRuntime,
    TasksFinished,
    TaskSummary,
    TaskSummaryFinished,
    TasksUnfinished,
    managed_task,
)
from haiway.utils.pagination import Paginated
from tests.managed_helpers import (
    CountingStore,
    Gate,
    LeasedStore,
    Output,
    Request,
    holding,
    wait_for,
)

ANY = TaskFilter()


@managed_task(name="work")
async def work(request: Request) -> Output:
    if request.n < 0:
        raise ValueError("secret")

    return Output(n=request.n)


@managed_task(name="other")
async def other(request: Request) -> Output:
    return Output()


async def spawn_in_order(
    count: int,
    *,
    first: int = 0,
) -> list[ManagedTask[Output]]:
    tasks: list[ManagedTask[Output]] = []
    for index in range(first, first + count):
        tasks.append(await ManagedTasks.spawn(work(Request(n=index))))
        # creation times are microseconds of the wall clock: never equal for two spawns
        await asyncio.sleep(0.002)

    return tasks


async def traversal(
    limit: int,
    *,
    matching: TaskFilter = ANY,
) -> list[Paginated[TaskSummary]]:
    pages: list[Paginated[TaskSummary]] = []
    pagination: Pagination = Pagination.of(limit=limit)
    while True:
        page = await ManagedTasks.tasks(pagination, matching=matching)
        pages.append(page)
        if page.token is None:
            return pages

        pagination = page.pagination


def identifiers(
    pages: list[Paginated[TaskSummary]],
) -> list[UUID]:
    return [summary.identifier for page in pages for summary in page]


# --- FR-94: the call ------------------------------------------------------------------------


@mark.asyncio
async def test_listing_outside_any_scope_raises_state_missing() -> None:
    # FR-94.1
    with raises(ContextStateMissing):
        await ManagedTasks.tasks(Pagination.of(limit=10))


@mark.parametrize(
    "pagination",
    [
        Pagination.of(limit=0),
        Pagination.of(limit=-1),
        Pagination.of(limit=1001),
        Pagination.of(limit=10, token=7),
        Pagination.of(limit=10, token="x"),
        Pagination.of(limit=10, token=UUID(int=1)),
        Pagination.of(limit=10, kind="a"),
        Pagination.of(
            limit=10,
            token=(
                '{"created": "2026-01-01T00:00:00",'
                ' "identifier": "00000000-0000-0000-0000-000000000001"}'
            ),
        ),
    ],
)
@mark.asyncio
async def test_invalid_pagination_is_refused_before_the_store(pagination: Pagination) -> None:
    # FR-94.2, FR-94.7
    store = CountingStore()
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        with raises(ValueError):
            await ManagedTasks.tasks(pagination)

    assert store.queries == 0


@mark.parametrize("limit", [1, 1000])
@mark.asyncio
async def test_the_limits_of_a_page_are_accepted(limit: int) -> None:
    # FR-94.2, D-115
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        assert len(await ManagedTasks.tasks(Pagination.of(limit=limit))) == 0


@mark.asyncio
async def test_retention_drops_a_finished_task_from_the_listing() -> None:
    # FR-94.3
    async with ctx.managed_tasks(
        TaskRuntime(store=InMemoryTaskStore(retention=0.05), tasks=(work,))
    ):
        task = await ManagedTasks.spawn(work(Request(n=1)))
        await task.outcome()
        assert [item.identifier for item in await ManagedTasks.tasks(Pagination.of(limit=10))] == [
            task.identifier
        ]
        await asyncio.sleep(0.2)
        assert len(await ManagedTasks.tasks(Pagination.of(limit=10))) == 0


@mark.asyncio
async def test_a_task_of_an_unlisted_name_spawned_by_another_runtime_is_listed() -> None:
    # FR-94.4
    store = InMemoryTaskStore()
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(other,))):
        spawned = await ManagedTasks.spawn(other(Request()))
        await spawned.outcome()
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
            listed = await ManagedTasks.tasks(Pagination.of(limit=10))

    assert [(item.identifier, item.name) for item in listed] == [(spawned.identifier, "other")]


@mark.asyncio
async def test_a_partial_page_has_no_token() -> None:
    # FR-94.5
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        spawned = await spawn_in_order(3)
        page = await ManagedTasks.tasks(Pagination.of(limit=10))

    assert [item.identifier for item in page] == [task.identifier for task in spawned]
    assert page.token is None
    assert page.has_next_page is False
    assert page.pagination.limit == 10


@mark.asyncio
async def test_a_page_of_summaries_holds_listing_data_only() -> None:
    # FR-98
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        done = await ManagedTasks.spawn(work(Request(n=1)))
        await done.outcome()
        failed = await ManagedTasks.spawn(work(Request(n=-1)))
        await failed.outcome()
        before = datetime.now(UTC)
        page = await ManagedTasks.tasks(Pagination.of(limit=10))

    summaries = {item.identifier: item for item in page}
    assert summaries[done.identifier].name == "work"
    assert summaries[done.identifier].attempts == 1
    status = summaries[done.identifier].status
    assert isinstance(status, TaskSummaryFinished) and status.outcome == "completed"
    assert status.finished <= before
    status = summaries[failed.identifier].status
    assert isinstance(status, TaskSummaryFinished) and status.outcome == "failed"
    assert "secret" not in repr(page)


@mark.asyncio
async def test_a_kept_instance_raises_once_the_block_is_exited() -> None:
    # FR-94.6
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))) as tasks:
        pass

    with ctx.updating(tasks):
        with raises(RuntimeError):
            await ManagedTasks.tasks(Pagination.of(limit=10))


@mark.asyncio
async def test_listing_from_another_event_loop_raises() -> None:
    # FR-34, FR-94
    errors: list[BaseException] = []
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))) as tasks:

        def other_loop() -> None:
            async def list_tasks() -> None:
                await tasks.listing(Pagination.of(limit=10), matching=TaskFilter())

            try:
                asyncio.run(list_tasks())

            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=other_loop)
        thread.start()
        await asyncio.to_thread(thread.join)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


@mark.asyncio
async def test_a_store_error_reaches_the_caller_unchanged() -> None:
    # FR-94, FR-82.1
    store = LeasedStore()
    store.query_failures = 1
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        with raises(OSError, match="query failed"):
            await ManagedTasks.tasks(Pagination.of(limit=10))

        assert len(await ManagedTasks.tasks(Pagination.of(limit=10))) == 0


@mark.asyncio
async def test_a_listing_is_served_while_closing_and_prolongs_the_close() -> None:
    # FR-94, D-139, D-142
    store = LeasedStore()
    gate = Gate(after=False)
    store.query_gate = gate
    loop = asyncio.get_running_loop()
    results: list[Paginated[TaskSummary]] = []
    started: list[asyncio.Task[object]] = []

    async def second() -> None:
        # once the close has begun: the first listing keeps it from ending
        while await ManagedTasks.available():
            await asyncio.sleep(0.001)

        results.append(await ManagedTasks.tasks(Pagination.of(limit=10)))
        loop.call_later(0.01, gate.release)

    async def body() -> None:
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
            started.append(asyncio.create_task(ManagedTasks.tasks(Pagination.of(limit=10))))
            await gate.arrived.wait()
            started.append(asyncio.create_task(second()))

    await wait_for(body())
    await wait_for(asyncio.gather(*started))
    assert len(results) == 1
    assert store.closed


# --- FR-97: order and pages -----------------------------------------------------------------


@mark.asyncio
async def test_pages_follow_the_creation_order_with_a_token_for_each_full_page() -> None:
    # FR-97.1
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        spawned = await spawn_in_order(5)
        pages = await traversal(2)

    assert [len(page) for page in pages] == [2, 2, 1]
    assert [page.token is None for page in pages] == [False, False, True]
    assert [page.has_next_page for page in pages] == [True, True, False]
    assert identifiers(pages) == [task.identifier for task in spawned]


@mark.asyncio
async def test_a_full_last_page_leads_to_one_empty_page() -> None:
    # FR-97.2
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        spawned = await spawn_in_order(4)
        pages = await traversal(2)

    assert [len(page) for page in pages] == [2, 2, 0]
    assert pages[1].has_next_page is True
    assert pages[2].token is None and pages[2].has_next_page is False
    assert identifiers(pages) == [task.identifier for task in spawned]


@mark.asyncio
async def test_the_token_is_the_position_of_the_last_task_listed() -> None:
    # FR-97
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        spawned = await spawn_in_order(3)
        page = await ManagedTasks.tasks(Pagination.of(limit=2))

    assert isinstance(page.token, str)
    keyset = TaskKeyset.from_json(page.token)
    assert keyset.identifier == spawned[1].identifier
    assert keyset.created == page[1].created


@mark.asyncio
async def test_tasks_inserted_by_other_runtimes_are_listed_once_each_across_pages() -> None:
    # FR-97.3 (equal creation times: test_managed_task_store_memory.py)
    store = InMemoryTaskStore()
    created = [
        NewTask(
            identifier=UUID(int=number),
            name="work",
            argument="{}",
            retry=TaskRetryPolicy(),
            time_limit=None,
            origin=None,
        )
        for number in (3, 1, 2)
    ]
    async with holding(store) as holder:
        for task in created:
            await holder.insert(task)

    # the tasks are of the unlisted name, no runtime of this block runs them
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(other,))):
        listed = identifiers(await traversal(1))

    assert sorted(listed) == [UUID(int=1), UUID(int=2), UUID(int=3)]
    assert len(listed) == 3


@mark.asyncio
async def test_tasks_created_at_one_instant_are_listed_by_identifier_across_pages(
    monkeypatch: MonkeyPatch,
) -> None:
    # FR-97.3
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("haiway.context.managed.memory._wall_now", lambda: instant)
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        spawned = [await ManagedTasks.spawn(work(Request(n=n))) for n in range(6)]
        pages = await traversal(1)

    assert {summary.created for page in pages for summary in page} == {instant}
    assert identifiers(pages) == sorted(task.identifier for task in spawned)


@mark.asyncio
async def test_a_traversal_returns_every_task_present_throughout_once() -> None:
    # FR-97.4
    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        initial = await spawn_in_order(50)
        returned: list[UUID] = []
        pagination = Pagination.of(limit=7)
        later: int = 100
        while True:
            page = await ManagedTasks.tasks(pagination)
            returned.extend(item.identifier for item in page)
            # tasks inserted and finished between the calls
            await spawn_in_order(2, first=later)
            later += 2
            if page.token is None:
                break

            pagination = page.pagination

    assert len(returned) == len(set(returned))
    assert {task.identifier for task in initial} <= set(returned)


@mark.asyncio
async def test_a_token_continues_a_listing_with_another_filter() -> None:
    # FR-97.5, FR-95
    async with ctx.managed_tasks(TaskRuntime(tasks=(work, other))):
        spawned = await spawn_in_order(4)
        await (await ManagedTasks.spawn(other(Request()))).outcome()
        first = await ManagedTasks.tasks(Pagination.of(limit=2))
        second = await ManagedTasks.tasks(
            first.pagination,
            matching=TaskFilter(names={"work"}),
        )
        everything = await ManagedTasks.tasks(Pagination.of(limit=10))

    assert [item.identifier for item in second] == [task.identifier for task in spawned[2:]]
    assert len(everything) == 5


# --- FR-95: filters through the runtime -----------------------------------------------------


@mark.asyncio
async def test_filters_select_by_name_status_and_outcome() -> None:
    # FR-95.1
    release = asyncio.Event()

    @managed_task(name="held")
    async def held(request: Request) -> Output:
        await release.wait()
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(work, held))):
        done = await ManagedTasks.spawn(work(Request(n=1)))
        failed = await ManagedTasks.spawn(work(Request(n=-1)))
        waiting = await ManagedTasks.spawn(held(Request()))
        await done.outcome()
        await failed.outcome()

        async def listed(matching: TaskFilter) -> set[UUID]:
            return {
                item.identifier
                for item in await ManagedTasks.tasks(Pagination.of(limit=10), matching=matching)
            }

        assert await listed(TaskFilter(names={"held"})) == {waiting.identifier}
        assert await listed(TaskFilter(names=set())) == set()
        assert await listed(TaskFilter(status=TasksFinished(outcomes={"failed"}))) == {
            failed.identifier
        }
        assert await listed(TaskFilter(status=TasksUnfinished(statuses={"running", "queued"}))) == {
            waiting.identifier
        }
        assert len(await listed(TaskFilter())) == 3
        release.set()
