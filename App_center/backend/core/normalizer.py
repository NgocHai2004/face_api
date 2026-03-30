"""
Event Normalizer - chuẩn hóa raw event từ producer thành NormalizedEvent
"""
from datetime import datetime, timezone

from .models import (
    EventMetadata,
    EventPriority,
    EventTopic,
    EventType,
    NormalizedEvent,
    RawEvent,
    TYPE_TO_TOPIC,
)


class EventNormalizer:
    """
    Nhận RawEvent hoặc dict từ producer, trả về NormalizedEvent chuẩn hóa.
    - Tự suy ra topic từ type nếu không được cung cấp
    - Map priority về giá trị hợp lệ (mặc định: medium)
    - Đảm bảo timestamp luôn có
    """

    VALID_TYPES      = {e.value for e in EventType}
    VALID_PRIORITIES = {e.value for e in EventPriority}
    VALID_TOPICS     = {e.value for e in EventTopic}

    def normalize(self, raw: RawEvent) -> NormalizedEvent:
        event_type = self._resolve_type(raw.type)
        topic      = self._resolve_topic(raw.topic, event_type)
        priority   = self._resolve_priority(raw.priority)

        return NormalizedEvent(
            source   = raw.source.strip(),
            type     = event_type,
            topic    = topic,
            priority = priority,
            payload  = raw.payload,
            metadata = EventMetadata(
                received_at = datetime.now(timezone.utc),
                normalized  = True,
                version     = "1.0",
            ),
        )

    def normalize_dict(self, data: dict) -> NormalizedEvent:
        """Tiện ích: nhận dict thô (từ WebSocket JSON), tự parse và normalize"""
        raw = RawEvent(**data)
        return self.normalize(raw)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_type(self, raw_type: str) -> str:
        """Nếu type không hợp lệ, fallback về 'custom'"""
        cleaned = raw_type.strip().lower().replace(" ", "_")
        return cleaned if cleaned in self.VALID_TYPES else EventType.CUSTOM.value

    def _resolve_topic(self, raw_topic: str | None, event_type: str) -> str:
        """
        Nếu topic được cung cấp và hợp lệ → dùng luôn
        Nếu không → suy ra từ event_type qua TYPE_TO_TOPIC mapping
        Fallback về 'security' nếu không tìm thấy
        """
        if raw_topic:
            cleaned = raw_topic.strip().lower()
            if cleaned in self.VALID_TOPICS:
                return cleaned

        # Suy ra từ type
        try:
            et = EventType(event_type)
            mapped = TYPE_TO_TOPIC.get(et)
            return mapped.value if mapped else EventTopic.SECURITY.value
        except ValueError:
            return EventTopic.SECURITY.value

    def _resolve_priority(self, raw_priority: str | None) -> str:
        if raw_priority:
            cleaned = raw_priority.strip().lower()
            if cleaned in self.VALID_PRIORITIES:
                return cleaned
        return EventPriority.MEDIUM.value
