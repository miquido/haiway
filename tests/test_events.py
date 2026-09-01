import asyncio
from asyncio import Task, get_running_loop
from collections.abc import Callable

from pytest import mark, raises

from haiway import ContextMissing, State, ctx
from haiway.context import EventsSubscription
from haiway.context.events import ContextEvents


async def _until(
    condition: Callable[[], bool],
    /,
    *,
    timeout: float = 5.0,
) -> None:
    """
    Wait for a condition instead of guessing how many loop iterations it needs.

    A fixed count of ``await sleep(0)`` happens to be enough only for the
    current arrangement of await points - one more hop anywhere upstream and the
    barrier silently stops holding. The timeout only guards against a hang.
    """
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0)


class OrderCreated(State):
    order_id: str
    amount: float


class UserActivity(State):
    user_id: str
    action: str


class PaymentEvent(State):
    payment_id: str
    status: str


@mark.asyncio
async def test_basic_send_and_subscribe():
    async with ctx.scope("test"):
        received_events = []

        # Start subscriber
        subscription = ctx.subscribe(OrderCreated)

        async def subscriber():
            async for event in subscription:
                received_events.append(event)
                if len(received_events) >= 2:
                    break

        task = ctx.spawn(subscriber)

        # Send events
        ctx.send(OrderCreated(order_id="123", amount=99.99))
        ctx.send(OrderCreated(order_id="456", amount=149.99))

        # Wait to complete
        await task

        # Verify events received
        assert len(received_events) == 2
        assert received_events[0].order_id == "123"
        assert received_events[0].amount == 99.99
        assert received_events[1].order_id == "456"
        assert received_events[1].amount == 149.99


@mark.asyncio
async def test_multiple_subscribers_same_type():
    async with ctx.scope("test"):
        received_1 = []
        received_2 = []

        # Create subscriptions before spawning tasks
        subscription_1 = ctx.subscribe(OrderCreated)
        subscription_2 = ctx.subscribe(OrderCreated)

        async def subscriber_1():
            async for event in subscription_1:
                received_1.append(event)
                break

        async def subscriber_2():
            async for event in subscription_2:
                received_2.append(event)
                break

        task_1 = ctx.spawn(subscriber_1)
        task_2 = ctx.spawn(subscriber_2)

        # Send event
        ctx.send(OrderCreated(order_id="789", amount=199.99))

        # Wait for subscribers to complete
        await task_1
        await task_2

        # Both subscribers should receive the same event
        assert len(received_1) == 1
        assert len(received_2) == 1
        assert received_1[0].order_id == "789"
        assert received_2[0].order_id == "789"


@mark.asyncio
async def test_multiple_event_types():
    async with ctx.scope("test"):
        # Subscribers for different event types
        orders = []
        activities = []

        # Create subscriptions before spawning tasks
        order_subscription = ctx.subscribe(OrderCreated)
        activity_subscription = ctx.subscribe(UserActivity)

        async def order_subscriber():
            async for event in order_subscription:
                orders.append(event)
                if len(orders) >= 2:
                    break

        async def activity_subscriber():
            async for event in activity_subscription:
                activities.append(event)
                if len(activities) >= 2:
                    break

        task_1 = ctx.spawn(order_subscriber)
        task_2 = ctx.spawn(activity_subscriber)

        # Send different event types
        ctx.send(OrderCreated(order_id="100", amount=50.0))
        ctx.send(UserActivity(user_id="user1", action="login"))
        ctx.send(OrderCreated(order_id="101", amount=75.0))
        ctx.send(UserActivity(user_id="user2", action="logout"))

        # Wait for subscribers to complete
        await task_1
        await task_2

        # Verify each subscriber only received its event type
        assert len(orders) == 2
        assert len(activities) == 2
        assert orders[0].order_id == "100"
        assert orders[1].order_id == "101"
        assert activities[0].action == "login"
        assert activities[1].action == "logout"


@mark.asyncio
async def test_event_ordering_is_fifo():
    async with ctx.scope("test"):
        received = []

        # Create subscription before spawning task
        subscription = ctx.subscribe(OrderCreated)

        async def subscriber():
            async for event in subscription:
                received.append(event.order_id)
                if len(received) >= 10:
                    break

        task = ctx.spawn(subscriber)

        # Send events in specific order
        for i in range(10):
            ctx.send(OrderCreated(order_id=str(i), amount=float(i)))

        # Wait for completion
        await task

        # Verify FIFO order
        assert received == [str(i) for i in range(10)]


@mark.asyncio
async def test_no_subscribers_no_memory_leak():
    async with ctx.scope("test"):
        events: ContextEvents = ContextEvents._context.get()

        for i in range(10_000):
            ctx.send(OrderCreated(order_id=str(i), amount=float(i)))

        # events without subscribers are discarded instead of being retained
        assert events._threads == {}


@mark.asyncio
async def test_context_isolation():
    # Test that events in different root contexts are isolated
    # First root context
    async with ctx.scope("context1"):
        context1_events = []

        # Create subscription before spawning task
        subscription1 = ctx.subscribe(OrderCreated)

        async def subscriber1():
            async for event in subscription1:
                context1_events.append(event)
                if len(context1_events) >= 2:
                    break

        task1 = ctx.spawn(subscriber1)

        ctx.send(OrderCreated(order_id="ctx1_event1", amount=1.0))
        ctx.send(OrderCreated(order_id="ctx1_event2", amount=2.0))

        await task1

    # Second root context - should have its own ContextEvents
    async with ctx.scope("context2"):
        context2_events = []

        # Create subscription before spawning task
        subscription2 = ctx.subscribe(OrderCreated)

        async def subscriber2():
            async for event in subscription2:
                context2_events.append(event)
                break  # Exit after first event

        task2 = ctx.spawn(subscriber2)

        ctx.send(OrderCreated(order_id="ctx2_event", amount=3.0))

        await task2

        # Context 2 should only see its own event
        assert len(context2_events) == 1
        assert context2_events[0].order_id == "ctx2_event"

    # Verify context 1 only saw its own events
    assert len(context1_events) == 2
    assert context1_events[0].order_id == "ctx1_event1"
    assert context1_events[1].order_id == "ctx1_event2"


@mark.asyncio
async def test_missing_context_errors():
    # Test send outside any context
    with raises(ContextMissing):
        ctx.send(OrderCreated(order_id="fail", amount=0.0))

    # Test subscribe outside any context
    with raises(ContextMissing):
        ctx.subscribe(OrderCreated)

    # Note: ctx.scope automatically creates ContextEvents for root scopes,
    # so we can't test ContextMissing within a ctx.scope anymore


@mark.asyncio
async def test_subscription_cleanup_on_cancel():
    async with ctx.scope("test"):
        tasks: list[Task] = []
        subscriptions = []
        primed: list[int] = []

        # Create subscriptions before spawning tasks
        for index in range(5):
            subscription = ctx.subscribe(OrderCreated)
            subscriptions.append(subscription)

            async def subscriber(sub=subscription, index=index):
                async for _ in sub:
                    primed.append(index)

            tasks.append(ctx.spawn(subscriber))

        # receiving a priming event proves every subscriber reached its
        # subscription and looped back to awaiting the next one
        ctx.send(OrderCreated(order_id="priming", amount=0.0))
        await _until(lambda: len(primed) == 5)

        # Cancel all tasks
        for task in tasks:
            task.cancel()

        # Wait for cancellation
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Send event - should not cause issues even though subscribers are gone
        ctx.send(OrderCreated(order_id="after_cancel", amount=0.0))


@mark.asyncio
async def test_cancelled_subscriber_keeps_delivery_for_others():
    async with ctx.scope("test"):
        cancelled_subscription = ctx.subscribe(OrderCreated)
        kept_subscription = ctx.subscribe(OrderCreated)
        received: list[OrderCreated] = []
        cancelled_received: list[OrderCreated] = []

        async def cancelled_subscriber():
            async for event in cancelled_subscription:
                cancelled_received.append(event)

        async def kept_subscriber():
            async for event in kept_subscription:
                received.append(event)

        cancelled_task: Task = ctx.spawn(cancelled_subscriber)
        kept_task: Task = ctx.spawn(kept_subscriber)

        # a priming event both subscribers observe proves they are awaiting the
        # shared future before it gets cancelled out from under one of them
        ctx.send(OrderCreated(order_id="priming", amount=0.0))
        await _until(lambda: len(cancelled_received) == 1 and len(received) == 1)

        cancelled_task.cancel()
        try:
            await cancelled_task
        except asyncio.CancelledError:
            pass

        # cancelling one subscriber must not break the shared event delivery
        ctx.send(OrderCreated(order_id="after_cancel", amount=1.0))
        await _until(lambda: len(received) == 2)

        assert [event.order_id for event in received] == ["priming", "after_cancel"]

        kept_task.cancel()


@mark.asyncio
async def test_subscription_after_close_does_not_block():
    async def scope_with_late_subscriber() -> None:
        async with ctx.scope("test"):

            async def late_subscriber():
                # subscribe once the scope body already completed
                await asyncio.sleep(0.01)
                async for _ in ctx.subscribe(OrderCreated):
                    pass

            ctx.spawn(late_subscriber)

    # a subscription created after the event bus closed has to finish
    # immediately instead of waiting for an event that can never arrive
    await asyncio.wait_for(scope_with_late_subscriber(), timeout=1.0)


@mark.asyncio
async def test_concurrent_send_and_receive():
    async with ctx.scope("test"):
        received_count = 0

        # Create subscription before spawning task
        subscription = ctx.subscribe(OrderCreated)

        async def subscriber():
            nonlocal received_count
            async for _ in subscription:
                received_count += 1
                if received_count >= 100:
                    break

        async def sender():
            for i in range(100):
                ctx.send(OrderCreated(order_id=str(i), amount=float(i)))
                if i % 10 == 0:
                    await asyncio.sleep(0)  # Yield control

        # Start subscriber
        sub_task = ctx.spawn(subscriber)

        # Run sender
        await sender()

        # Wait for completion
        await sub_task

        # Should have received all 100 events
        assert received_count == 100


@mark.skipif(not __debug__, reason="assertions are stripped in optimized builds")
@mark.asyncio
async def test_reentrant_context_not_allowed():
    events_ctx = ContextEvents(loop=get_running_loop())

    async with events_ctx:
        # Try to enter the same context again
        with raises(AssertionError, match="Context reentrance is not allowed"):
            async with events_ctx:
                pass


@mark.asyncio
async def test_subscription_iterator_is_async():
    async with ctx.scope("test"):
        subscription = ctx.subscribe(OrderCreated)

        # Verify it's an EventsSubscription
        assert isinstance(subscription, EventsSubscription)

        # Verify it has async iterator methods
        assert hasattr(subscription, "__anext__")


@mark.asyncio
async def test_events_with_subscribers_joining_late():
    async with ctx.scope("test"):
        early_events = []
        late_events = []

        # Create early subscription before spawning task
        early_subscription = ctx.subscribe(OrderCreated)

        async def early_subscriber():
            async for event in early_subscription:
                early_events.append(event)
                if len(early_events) >= 4:
                    return

        # Start early subscriber
        early_task = ctx.spawn(early_subscriber)

        # Send some events
        ctx.send(OrderCreated(order_id="1", amount=10.0))
        ctx.send(OrderCreated(order_id="2", amount=20.0))

        # the late subscription starts at the current tail either way, but wait
        # for the early subscriber so the test states what it actually relies on
        await _until(lambda: len(early_events) == 2)

        # Create late subscription before spawning task
        late_subscription = ctx.subscribe(OrderCreated)

        # Start late subscriber
        async def late_subscriber():
            async for event in late_subscription:
                late_events.append(event)
                if len(late_events) >= 2:
                    return

        late_task = ctx.spawn(late_subscriber)

        # Send more events
        ctx.send(OrderCreated(order_id="3", amount=30.0))
        ctx.send(OrderCreated(order_id="4", amount=40.0))

        # Wait for completion
        await early_task
        await late_task

        # Early subscriber should see all events
        assert len(early_events) == 4
        # Late subscriber should only see events after it subscribed
        assert len(late_events) == 2
        assert late_events[0].order_id == "3"
        assert late_events[1].order_id == "4"


async def _received_within(
    subscription: EventsSubscription[OrderCreated],
    timeout: float,
) -> OrderCreated | None:
    try:
        return await asyncio.wait_for(anext(subscription), timeout)

    except TimeoutError:
        return None


# a delivery which is expected gets a generous bound - ordering is established
# through the ready events below, so the timeout only guards against a hang
_DELIVERED_TIMEOUT: float = 5.0
# a delivery which must not happen can only be observed by waiting for it
_SKIPPED_TIMEOUT: float = 0.25


@mark.asyncio
async def test_events_are_not_delivered_to_sibling_scopes():
    received: dict[str, OrderCreated | None] = {}
    subscribed = asyncio.Event()

    async def consumer() -> None:
        async with ctx.scope("request-consumer"):
            subscription = ctx.subscribe(OrderCreated)
            subscribed.set()
            received["consumer"] = await _received_within(subscription, _SKIPPED_TIMEOUT)

    async def producer() -> None:
        await subscribed.wait()
        async with ctx.scope("request-producer"):
            ctx.send(OrderCreated(order_id="sibling", amount=1.0))

    async with ctx.scope("server"):
        await asyncio.gather(consumer(), producer())

    assert received["consumer"] is None


@mark.asyncio
async def test_broadcast_events_reach_sibling_scopes():
    received: dict[str, OrderCreated | None] = {}
    subscribed = asyncio.Event()

    async def consumer() -> None:
        async with ctx.scope("request-consumer"):
            subscription = ctx.subscribe(OrderCreated)
            subscribed.set()
            received["consumer"] = await _received_within(subscription, _DELIVERED_TIMEOUT)

    async def producer() -> None:
        await subscribed.wait()
        async with ctx.scope("request-producer"):
            ctx.send(
                OrderCreated(order_id="broadcast", amount=2.0),
                broadcast=True,
            )

    async with ctx.scope("server"):
        await asyncio.gather(consumer(), producer())

    assert received["consumer"] == OrderCreated(order_id="broadcast", amount=2.0)


@mark.asyncio
async def test_broadcast_events_do_not_cross_isolated_scopes():
    received: dict[str, OrderCreated | None] = {}
    subscribed = asyncio.Event()

    async def consumer() -> None:
        # an isolated scope gets its own event bus - nothing from the outer bus
        # can reach it, not even a broadcast
        async with ctx.scope("request-consumer", isolated=True):
            subscription = ctx.subscribe(OrderCreated)
            subscribed.set()
            received["consumer"] = await _received_within(subscription, _SKIPPED_TIMEOUT)

    async def producer() -> None:
        await subscribed.wait()
        async with ctx.scope("request-producer"):
            ctx.send(
                OrderCreated(order_id="broadcast", amount=2.0),
                broadcast=True,
            )

    async with ctx.scope("server"):
        await asyncio.gather(consumer(), producer())

    assert received["consumer"] is None


@mark.asyncio
async def test_events_are_delivered_to_enclosing_scopes():
    async with ctx.scope("server"):
        subscription = ctx.subscribe(OrderCreated)

        async def producer() -> None:
            async with ctx.scope("request"), ctx.scope("worker"):
                ctx.send(OrderCreated(order_id="nested", amount=3.0))

        # spawned through the scope so a failing assertion cannot leak the task
        # into loop teardown
        task: Task[None] = ctx.spawn(producer)
        received = await _received_within(subscription, _DELIVERED_TIMEOUT)
        await task

    assert received == OrderCreated(order_id="nested", amount=3.0)


@mark.asyncio
async def test_events_are_not_delivered_to_nested_scopes():
    received: dict[str, OrderCreated | None] = {}
    subscribed = asyncio.Event()

    async def consumer() -> None:
        async with ctx.scope("worker"):
            subscription = ctx.subscribe(OrderCreated)
            subscribed.set()
            received["worker"] = await _received_within(subscription, _SKIPPED_TIMEOUT)

    async with ctx.scope("server"):

        async def producer() -> None:
            await subscribed.wait()
            ctx.send(OrderCreated(order_id="downwards", amount=4.0))

        await asyncio.gather(consumer(), producer())

    assert received["worker"] is None


@mark.asyncio
async def test_events_are_delivered_within_spawned_tasks():
    async with ctx.scope("server"):
        subscription = ctx.subscribe(OrderCreated)

        async def producer() -> None:
            ctx.send(OrderCreated(order_id="spawned", amount=5.0))

        ctx.spawn(producer)
        received = await _received_within(subscription, _DELIVERED_TIMEOUT)

    assert received == OrderCreated(order_id="spawned", amount=5.0)


@mark.asyncio
async def test_skipped_events_do_not_block_later_delivery():
    received: dict[str, OrderCreated | None] = {}
    subscribed = asyncio.Event()

    async def consumer() -> None:
        async with ctx.scope("request-consumer"):
            subscription = ctx.subscribe(OrderCreated)
            subscribed.set()
            received["consumer"] = await _received_within(subscription, _DELIVERED_TIMEOUT)

    async def producer() -> None:
        await subscribed.wait()
        async with ctx.scope("request-producer"):
            # not addressed to the sibling consumer
            ctx.send(OrderCreated(order_id="skipped", amount=6.0))
            ctx.send(
                OrderCreated(order_id="delivered", amount=7.0),
                broadcast=True,
            )

    async with ctx.scope("server"):
        await asyncio.gather(consumer(), producer())

    assert received["consumer"] == OrderCreated(order_id="delivered", amount=7.0)


@mark.asyncio
async def test_subscription_ends_when_isolated_scope_exits():
    # the nested scope owns both the bus and the task consuming the subscription,
    # so closing the bus on its exit is what lets the task group join that task
    subscribed = asyncio.Event()
    ended = asyncio.Event()

    async def consumer() -> None:
        async for _ in ctx.subscribe(OrderCreated):
            pass  # pragma: no cover - no event is ever sent

        ended.set()

    async with ctx.scope("root"):
        async with ctx.scope("nested", isolated=True):
            ctx.spawn(consumer)
            subscribed.set()
            await _until(subscribed.is_set)

        # exiting the nested scope ended the subscription made within it
        assert ended.is_set()


@mark.asyncio
async def test_subscription_delivers_pending_event_before_ending():
    # a scope exiting must not discard an event which already arrived
    received: list[OrderCreated] = []

    async def consumer() -> None:
        async for event in ctx.subscribe(OrderCreated):
            received.append(event)

    async with ctx.scope("root"):
        async with ctx.scope("nested", isolated=True):
            ctx.spawn(consumer)
            await _until(lambda: ContextEvents._context.get()._threads != {})
            ctx.send(OrderCreated(order_id="delivered", amount=1.0))

    assert received == [OrderCreated(order_id="delivered", amount=1.0)]


@mark.asyncio
async def test_subscription_ends_gracefully_outside_of_any_scope() -> None:
    subscription: EventsSubscription[OrderCreated]

    async with ctx.scope("scope"):
        subscription = ctx.subscribe(OrderCreated)

    # the subscription outlived every scope - a finished generator ends its
    # iteration, it does not fail for the lack of a context to end within
    with raises(StopAsyncIteration):
        await anext(subscription)


@mark.asyncio
async def test_subscription_is_bound_to_the_subscribing_scope() -> None:
    received: list[str] = []

    async def consume() -> None:
        subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)
        # a nested scope opening and closing beside the subscription must not end
        # it - the scope which created it is the one which owns it
        async with ctx.scope("nested"):
            pass

        received.append((await anext(subscription)).order_id)

    async with ctx.scope("scope"):
        task: Task[None] = ctx.spawn(consume)
        await _until(lambda: bool(ContextEvents._context.get()._threads))
        ctx.send(OrderCreated(order_id="order", amount=42.0))
        await task

    assert received == ["order"]


@mark.asyncio
async def test_subscription_athrow_rejects_malformed_calls_without_ending() -> None:
    async with ctx.scope("scope"):
        subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)

        with raises(TypeError):
            await subscription.athrow(ValueError("exception"), "value")

        # the call was rejected before anything was thrown, so the subscription
        # is still the usable one it was before
        ctx.send(OrderCreated(order_id="order", amount=42.0))
        assert (await anext(subscription)).order_id == "order"


@mark.asyncio
async def test_subscription_aclose_releases_a_waiting_iteration() -> None:
    outcome: list[str] = []

    async with ctx.scope("scope"):
        subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)

        suspended = asyncio.Event()

        async def consume() -> None:
            suspended.set()
            try:
                await anext(subscription)

            except StopAsyncIteration:
                outcome.append("ended")

        waiting: Task[None] = ctx.spawn(consume)
        # `suspended` is set right before `anext` with no await in between, so the
        # task can only hand control back from within the iteration it started -
        # one which is not done here is parked on the futures that call races
        await suspended.wait()
        assert not waiting.done()

        # a suspended `__anext__` keeps the futures it races as locals, so closing
        # has to release it rather than only clearing what the subscription holds
        await subscription.aclose()
        async with asyncio.timeout(5.0):  # a close which does not release it hangs
            await waiting

    assert outcome == ["ended"]


@mark.asyncio
async def test_subscription_aclose_ends_further_iteration() -> None:
    async with ctx.scope("scope"):
        subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)

        await subscription.aclose()
        await subscription.aclose()  # closing twice is not an error

        # a pending event is not delivered to a subscription which was closed
        ctx.send(OrderCreated(order_id="order", amount=42.0))
        with raises(StopAsyncIteration):
            await anext(subscription)


@mark.asyncio
async def test_aclosing_ends_the_subscription() -> None:
    received: list[str] = []

    async with ctx.scope("scope"):
        subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)

        async with ctx.closing(subscription) as orders:
            ctx.send(OrderCreated(order_id="order", amount=42.0))
            async for order in orders:
                received.append(order.order_id)
                break  # left early - the subscription is released right here

        with raises(StopAsyncIteration):
            await anext(subscription)

    assert received == ["order"]


@mark.asyncio
async def test_skipped_event_does_not_discard_pending_delivery_on_closing() -> None:
    # a consumer busy elsewhere while the events arrive finds them queued behind
    # one which is not addressed to it - a scope closing must deliver the rest
    # instead of ending on the first skipped event
    received: list[str] = []
    subscribed = asyncio.Event()
    sent = asyncio.Event()
    resumed = asyncio.Event()

    async def producer() -> None:
        await subscribed.wait()
        async with ctx.scope("request-producer"):  # sibling of the consumer scope
            # not addressed to the sibling consumer
            ctx.send(OrderCreated(order_id="skipped", amount=6.0))
            ctx.send(
                OrderCreated(order_id="delivered", amount=7.0),
                broadcast=True,
            )

        sent.set()

    async def consumer() -> None:
        async with ctx.scope("request-consumer"):
            subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)

            async def consume() -> None:
                await resumed.wait()  # busy elsewhere while both events arrive
                async for event in subscription:
                    received.append(event.order_id)

            ctx.spawn(consume)
            subscribed.set()
            await sent.wait()
            # the task may only resume when the scope is already closing
            resumed.set()

    async with ctx.scope("server"):
        await asyncio.gather(consumer(), producer())

    assert received == ["delivered"]


@mark.asyncio
async def test_events_arrived_before_closing_are_delivered_after_it() -> None:
    # the subscribing scope has already left - everything which arrived before
    # is still delivered, and only a skipped tail ends the iteration
    received: list[str] = []

    async with ctx.scope("server"):
        subscription: EventsSubscription[OrderCreated]
        async with ctx.scope("request-consumer"):
            subscription = ctx.subscribe(OrderCreated)

        async with ctx.scope("request-producer"):  # sibling of the consumer scope
            ctx.send(OrderCreated(order_id="skipped", amount=6.0))
            ctx.send(
                OrderCreated(order_id="delivered", amount=7.0),
                broadcast=True,
            )

        async for event in subscription:
            received.append(event.order_id)

    assert received == ["delivered"]


@mark.asyncio
async def test_subscription_rejects_concurrent_iteration() -> None:
    async with ctx.scope("scope"):
        subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)
        first: Task[OrderCreated] = asyncio.ensure_future(anext(subscription))
        await _until(lambda: not first.done() and subscription._running)

        # the chain position is shared - a second iteration would deliver the
        # very same event instead of the next one
        with raises(RuntimeError):
            await anext(subscription)

        with raises(RuntimeError):
            await subscription.asend(None)

        ctx.send(OrderCreated(order_id="order", amount=42.0))

        assert await first == OrderCreated(order_id="order", amount=42.0)
        # the guard is released with the iteration - the next one proceeds
        ctx.send(OrderCreated(order_id="next", amount=43.0))
        assert await anext(subscription) == OrderCreated(order_id="next", amount=43.0)


@mark.asyncio
async def test_subscription_aclose_is_allowed_during_iteration() -> None:
    # unlike iterating, ending a running subscription is what releases it
    async with ctx.scope("scope"):
        subscription: EventsSubscription[OrderCreated] = ctx.subscribe(OrderCreated)
        iteration: Task[OrderCreated] = asyncio.ensure_future(anext(subscription))
        await _until(lambda: not iteration.done() and subscription._running)

        await subscription.aclose()

        with raises(StopAsyncIteration):
            await iteration
