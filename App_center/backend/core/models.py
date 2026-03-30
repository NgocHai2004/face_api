"""
Pydantic models cho Event Hub - định nghĩa schema chuẩn hóa sự kiện
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    FACE_RECOGNITION = "face_recognition"
    FINGERPRINT      = "fingerprint"
    CUSTOM           = "custom"          # fallback cho mọi type không xác định


class EventPriority(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
    URGENT = "urgent"


class EventTopic(str, Enum):
    SECURITY = "security"
    CUSTOM   = "custom"          # fallback topic


# ---------------------------------------------------------------------------
# Topic mapping: type -> topic (mặc định)
# ---------------------------------------------------------------------------

TYPE_TO_TOPIC: dict[EventType, EventTopic] = {
    EventType.FACE_RECOGNITION: EventTopic.SECURITY,
    EventType.FINGERPRINT:      EventTopic.SECURITY,
    EventType.CUSTOM:           EventTopic.CUSTOM,
}


# ---------------------------------------------------------------------------
# Raw event - gửi từ producer (linh hoạt, chưa chuẩn hóa)
# ---------------------------------------------------------------------------

class RawEvent(BaseModel):
    """Schema producer gửi lên - chỉ cần source + type + payload"""
    source:   str              = Field(..., description="Tên/ID thiết bị gửi, vd: camera_01")
    type:     str              = Field(..., description="Loại sự kiện, vd: face_recognition")
    priority: str | None       = Field(None, description="Độ ưu tiên: low/medium/high/urgent")
    topic:    str | None       = Field(None, description="Topic tùy chỉnh (nếu không điền, tự suy ra từ type)")
    payload:  dict[str, Any]   = Field(default_factory=dict, description="Dữ liệu thô từ thiết bị")

    model_config = {"extra": "allow"}  # Cho phép thêm field tùy ý


# ---------------------------------------------------------------------------
# Normalized event - schema thống nhất sau khi qua normalizer
# ---------------------------------------------------------------------------

class EventMetadata(BaseModel):
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    normalized:  bool     = True
    version:     str      = "1.0"


class NormalizedEvent(BaseModel):
    """Schema chuẩn hóa - consumer nhận được"""
    id:        UUID          = Field(default_factory=uuid4)
    timestamp: datetime      = Field(default_factory=lambda: datetime.now(timezone.utc))
    source:    str
    type:      str
    topic:     str
    priority:  str
    payload:   dict[str, Any]
    metadata:  EventMetadata = Field(default_factory=EventMetadata)

    def to_json(self) -> dict:
        """Serialize thành dict để gửi qua WebSocket"""
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class IngestResponse(BaseModel):
    success:  bool
    event_id: str
    message:  str


class RecentEventsResponse(BaseModel):
    topic:  str
    count:  int
    events: list[NormalizedEvent]


class TopicsResponse(BaseModel):
    topics: list[str]
    total_consumers: int
