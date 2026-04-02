"""
App_center/backend/core/database.py

MongoDB + Beanie ODM cho Event Hub.
Collection: events  (lưu NormalizedEvent vĩnh viễn)
"""
import os
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from beanie import Document, Indexed, init_beanie
from pydantic import Field

logger = logging.getLogger("event_hub.db")

MONGODB_URL     = os.getenv("MONGODB_URL",     "mongodb://localhost:28017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "event_hub")


# ---------------------------------------------------------------------------
# Beanie Document model — ánh xạ tới collection "events"
# ---------------------------------------------------------------------------

class EventDocument(Document):
    """Lưu mỗi NormalizedEvent thành 1 document MongoDB."""

    event_id:   str                     # UUID string
    timestamp:  datetime                = Field(default_factory=lambda: datetime.now(timezone.utc))
    source:     Indexed(str)            # index để query nhanh theo source
    type:       Indexed(str)            # index theo event type
    topic:      Indexed(str)            # index theo topic
    priority:   str
    payload:    dict[str, Any]          = Field(default_factory=dict)
    metadata:   dict[str, Any]          = Field(default_factory=dict)
    # Thêm TTL index tự động xóa sau 30 ngày (tùy chỉnh qua env)
    created_at: datetime                = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name            = "events"
        use_state_management = False

    @classmethod
    def from_normalized(cls, event) -> "EventDocument":
        """Tạo EventDocument từ NormalizedEvent (Pydantic model)."""
        data = event.model_dump(mode="json")
        return cls(
            event_id  = str(data["id"]),
            timestamp = event.timestamp,
            source    = data["source"],
            type      = data["type"],
            topic     = data["topic"],
            priority  = data["priority"],
            payload   = data.get("payload", {}),
            metadata  = data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Init function — gọi 1 lần trong lifespan
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Kết nối MongoDB và khởi tạo Beanie ODM."""
    connection_string = f"{MONGODB_URL}/{MONGODB_DB_NAME}"
    await init_beanie(
        connection_string=connection_string,
        document_models=[EventDocument],
    )
    # Tạo TTL index: tự xóa documents sau N ngày (mặc định 30)
    ttl_days = int(os.getenv("EVENT_TTL_DAYS", "30"))
    try:
        await EventDocument.get_motor_collection().create_index(
            "created_at",
            expireAfterSeconds=ttl_days * 86400,
            background=True,
        )
    except Exception as e:
        logger.warning("TTL index: %s", e)
    logger.info(
        "[MongoDB] Kết nối '%s', db='%s', TTL=%d ngày",
        MONGODB_URL, MONGODB_DB_NAME, ttl_days,
    )
