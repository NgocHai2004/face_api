"""
Event Bus - in-memory event bus dùng asyncio.Queue
- Nhận NormalizedEvent từ producers (WS hoặc REST)
- Lưu lịch sử per-topic (in-memory) + persist MongoDB
- Broadcast realtime tới tất cả consumers đang subscribe
"""
import asyncio
import logging
from collections import defaultdict, deque
from typing import Deque

from fastapi import WebSocket

from .models import NormalizedEvent

logger = logging.getLogger("event_hub.bus")

WILDCARD_TOPIC = "*"  # Consumer subscribe "*" nhận tất cả topics


class EventBus:
    """
    Singleton in-memory event bus.

    Sử dụng:
        bus = EventBus(max_queue_size=1000, max_history=100)
        await bus.publish(event)           # từ producer
        await bus.subscribe(ws, "security") # từ consumer WS
        bus.unsubscribe(ws, "security")    # khi consumer disconnect
    """

    def __init__(self, max_queue_size: int = 1000, max_history: int = 100):
        self._queue: asyncio.Queue[NormalizedEvent] = asyncio.Queue(maxsize=max_queue_size)
        # topic -> set of WebSocket connections
        self._subscribers: dict[str, set[WebSocket]] = defaultdict(set)
        # topic -> deque(NormalizedEvent) - lịch sử gần nhất
        self._history: dict[str, Deque[NormalizedEvent]] = defaultdict(
            lambda: deque(maxlen=max_history)
        )
        self._max_history = max_history
        self._running     = False
        self._dispatcher_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Khởi động background dispatcher task"""
        if not self._running:
            self._running = True
            self._dispatcher_task = asyncio.create_task(self._dispatch_loop())
            logger.info("EventBus dispatcher started")

    async def stop(self):
        """Dừng dispatcher task"""
        self._running = False
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
        logger.info("EventBus dispatcher stopped")

    # ------------------------------------------------------------------
    # Producer interface
    # ------------------------------------------------------------------

    async def publish(self, event: NormalizedEvent) -> bool:
        """
        Đưa event vào queue để dispatcher xử lý.
        Trả về False nếu queue đầy (bỏ event, không block).
        """
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.warning(
                "Queue full! Dropping event id=%s type=%s", event.id, event.type
            )
            return False

    # ------------------------------------------------------------------
    # Consumer interface
    # ------------------------------------------------------------------

    async def subscribe(self, ws: WebSocket, topic: str, send_history: bool = False):
        """Đăng ký WebSocket consumer vào topic (hoặc '*' để nhận tất cả)"""
        self._subscribers[topic].add(ws)
        logger.info("Consumer subscribed topic=%s total=%d", topic, len(self._subscribers[topic]))

        # Gửi lịch sử gần nhất chỉ khi được yêu cầu (mặc định tắt)
        if send_history:
            history = self.get_history(topic)
            if history:
                for evt in history:
                    await self._send_safe(ws, evt)

    def unsubscribe(self, ws: WebSocket, topic: str):
        """Hủy đăng ký consumer khỏi topic"""
        self._subscribers[topic].discard(ws)
        logger.info("Consumer unsubscribed topic=%s remaining=%d", topic, len(self._subscribers[topic]))

    def unsubscribe_all(self, ws: WebSocket):
        """Hủy consumer khỏi tất cả topics (dùng khi WS disconnect)"""
        for topic in list(self._subscribers.keys()):
            self._subscribers[topic].discard(ws)

    # ------------------------------------------------------------------
    # History / info
    # ------------------------------------------------------------------

    def get_history(self, topic: str, limit: int | None = None) -> list[NormalizedEvent]:
        """Lấy lịch sử events của topic"""
        if topic == WILDCARD_TOPIC:
            # Gộp tất cả topics, sort theo timestamp
            all_events: list[NormalizedEvent] = []
            for evts in self._history.values():
                all_events.extend(evts)
            all_events.sort(key=lambda e: e.timestamp)
            return all_events[-limit:] if limit else all_events

        events = list(self._history.get(topic, []))
        return events[-limit:] if limit else events

    def get_active_topics(self) -> list[str]:
        """Danh sách topics đang có sự kiện trong lịch sử"""
        return [t for t, h in self._history.items() if h]

    def clear_history(self, topic: str | None = None):
        """Xóa in-memory history. topic=None → xóa tất cả topics."""
        if topic is None:
            self._history.clear()
            logger.info("EventBus: cleared all in-memory history")
        elif topic in self._history:
            self._history[topic].clear()
            logger.info("EventBus: cleared history for topic=%s", topic)

    def get_total_consumers(self) -> int:
        """Tổng số consumer connections đang active"""
        seen = set()
        for ws_set in self._subscribers.values():
            seen.update(ws_set)
        return len(seen)

    def get_queue_size(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # Background dispatcher
    # ------------------------------------------------------------------

    async def _dispatch_loop(self):
        """Loop liên tục lấy event từ queue và broadcast tới consumers"""
        logger.info("Dispatcher loop running")
        while self._running:
            try:
                event: NormalizedEvent = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue  # Không có event, loop tiếp
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Dispatcher error: %s", exc)

    async def _dispatch(self, event: NormalizedEvent):
        """Gửi event tới consumers subscribe topic tương ứng và wildcard"""
        # Lưu vào in-memory history
        self._history[event.topic].append(event)

        # Persist vào MongoDB (fire-and-forget, không block dispatch)
        asyncio.ensure_future(self._persist_to_mongo(event))

        # Tập hợp tất cả consumers cần nhận (topic cụ thể + wildcard)
        targets: set[WebSocket] = set()
        targets.update(self._subscribers.get(event.topic, set()))
        targets.update(self._subscribers.get(WILDCARD_TOPIC, set()))

        if not targets:
            logger.debug("No consumers for topic=%s, event dropped to history only", event.topic)
            return

        # Broadcast song song tới tất cả consumers
        results = await asyncio.gather(
            *[self._send_safe(ws, event) for ws in targets],
            return_exceptions=True,
        )
        sent = sum(1 for r in results if r is True)
        logger.debug("Dispatched event id=%s topic=%s to %d/%d consumers", event.id, event.topic, sent, len(targets))

    async def _persist_to_mongo(self, event: NormalizedEvent):
        """Lưu event vào MongoDB. Bắt lỗi để không làm crash dispatcher."""
        try:
            from .database import EventDocument
            doc = EventDocument.from_normalized(event)
            await doc.insert()
            logger.debug("Persisted event id=%s to MongoDB", event.id)
        except Exception as exc:
            logger.warning("MongoDB persist failed for event id=%s: %r", event.id, exc)

    async def _send_safe(self, ws: WebSocket, event: NormalizedEvent) -> bool:
        """Gửi event tới 1 WebSocket, bắt lỗi nếu connection đã đóng"""
        try:
            await ws.send_json(event.to_json())
            return True
        except Exception as exc:
            logger.debug("Failed to send to consumer: %s", exc)
            # Cleanup stale connection
            self.unsubscribe_all(ws)
            return False


# ---------------------------------------------------------------------------
# Global singleton instance
# ---------------------------------------------------------------------------
event_bus = EventBus()
