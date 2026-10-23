"""聊天事件推送（fork:realtime）。

消息到达后端时，网页还在等下一轮 10 秒轮询 —— 这段时间里数据其实已经在
服务端了。这里提供一个极简的事件总线：账号的消息处理逻辑 publish，网页通过
SSE 订阅，收到事件后立刻拉一次最新消息。

为什么要自己写而不是直接用 asyncio.Queue：

- 账号任务跑在 Start.py 的主事件循环里；
- uvicorn 在**另一个线程**里跑自己的事件循环。

两边不是同一个 loop，直接跨线程往 asyncio.Queue 里塞会静默丢事件。
所以订阅时记录订阅方所属的 loop，publish 时用 ``call_soon_threadsafe`` 投递。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List, Tuple


class ChatEventBus:
    """进程内的聊天事件总线（单机多账号共用）。"""

    def __init__(self, max_queue: int = 200):
        self._max_queue = max_queue
        self._subscribers: List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers.append((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [
                (loop, q) for loop, q in self._subscribers if q is not queue
            ]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: Dict[str, Any]) -> None:
        """发布事件；从任意线程调用都安全。"""
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._deliver, queue, event)
            except RuntimeError:
                # 订阅方的 loop 已经关闭（页面关掉/服务重启中），忽略即可
                pass

    @staticmethod
    def _deliver(queue: asyncio.Queue, event: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # 消费端卡住时宁可丢事件，也不要让消息处理逻辑被拖住；
            # 前端还有轮询兜底。
            pass


chat_event_bus = ChatEventBus()
