import asyncio
from asyncio import Task

from pytest import mark, raises

from haiway import MissingContext, State, ctx
from haiway.context import EventSubscription
from haiway.context.events import EventsContext


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
        # Send events without any subscribers
        for i in range(100_000):
            ctx.send(OrderCreated(order_id=str(i), amount=float(i)))

        # Events should be discarded immediately
        # No way to directly verify, but this shouldn't consume memory


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

    # Second root context - should have its own EventsContext
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
    with raises(MissingContext):
        ctx.send(OrderCreated(order_id="fail", amount=0.0))

    # Test subscribe outside any context
    with raises(MissingContext):
        ctx.subscribe(OrderCreated)

    # Note: ctx.scope automatically creates EventsContext for root scopes,
    # so we can't test MissingContext within a ctx.scope anymore


@mark.asyncio
async def test_subscription_cleanup_on_cancel():
    async with ctx.scope("test"):
        tasks: list[Task] = []
        subscriptions = []

        # Create subscriptions before spawning tasks
        for _ in range(5):
            subscription = ctx.subscribe(OrderCreated)
            subscriptions.append(subscription)

            async def subscriber(sub=subscription):
                async for _ in sub:
                    pass  # Just consume events

            tasks.append(ctx.spawn(subscriber))

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


@mark.asyncio
async def test_reentrant_context_not_allowed():
    events_ctx = EventsContext()

    async with events_ctx:
        # Try to enter the same context again
        with raises(AssertionError, match="Context reentrance is not allowed"):
            async with events_ctx:
                pass


@mark.asyncio
async def test_subscription_iterator_is_async():
    async with ctx.scope("test"):
        subscription = ctx.subscribe(OrderCreated)

        # Verify it's an EventSubscription
        assert isinstance(subscription, EventSubscription)

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

        await asyncio.sleep(0.01)

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
