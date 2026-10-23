"""聊天事件总线（fork:realtime）单测。"""

import asyncio
import threading

from app.chat_events import ChatEventBus


def test_subscribe_and_unsubscribe():
    async def scenario():
        bus = ChatEventBus()
        loop = asyncio.get_running_loop()
        queue = bus.subscribe(loop)
        assert bus.subscriber_count == 1

        bus.publish({"type": "message", "chat_id": "c1"})
        await asyncio.sleep(0)  # call_soon_threadsafe 需要一次调度
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["chat_id"] == "c1"

        bus.unsubscribe(queue)
        assert bus.subscriber_count == 0
        bus.publish({"type": "message", "chat_id": "c2"})
        await asyncio.sleep(0)
        assert queue.empty()

    asyncio.run(scenario())


def test_publish_from_another_thread():
    """账号任务和 uvicorn 不在同一个 loop，跨线程投递必须不丢事件。"""

    async def scenario():
        bus = ChatEventBus()
        loop = asyncio.get_running_loop()
        queue = bus.subscribe(loop)

        def worker():
            bus.publish({"type": "message", "chat_id": "from-thread"})

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["chat_id"] == "from-thread"

    asyncio.run(scenario())


def test_multiple_subscribers_all_receive():
    async def scenario():
        bus = ChatEventBus()
        loop = asyncio.get_running_loop()
        first = bus.subscribe(loop)
        second = bus.subscribe(loop)

        bus.publish({"type": "message", "chat_id": "c"})
        await asyncio.sleep(0)

        assert (await asyncio.wait_for(first.get(), timeout=1))["chat_id"] == "c"
        assert (await asyncio.wait_for(second.get(), timeout=1))["chat_id"] == "c"

    asyncio.run(scenario())


def test_publish_without_subscribers_is_safe():
    bus = ChatEventBus()
    bus.publish({"type": "message"})  # 不应抛异常
