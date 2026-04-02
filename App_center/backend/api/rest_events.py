"""
REST API endpoints cho Event Hub.
- POST /events/ingest        → Producer gửi event qua HTTP
- GET  /events/recent        → Query lịch sử events
- GET  /events/topics        → Danh sách topics active
- GET  /health               → Health check + stats
"""
import logging
import os

from fastapi import APIRouter, HTTPException, Query

from core.event_bus import event_bus
from core.image_store import save_face_crop, get_image_url, get_hub_base_url
from core.models import (
    IngestResponse,
    NormalizedEvent,
    RawEvent,
    RecentEventsResponse,
    TopicsResponse,
)
from core.normalizer import EventNormalizer

logger = logging.getLogger("event_hub.rest")
router = APIRouter()
normalizer = EventNormalizer()


# ---------------------------------------------------------------------------
# POST /events/ingest - Producer gửi event qua HTTP
# ---------------------------------------------------------------------------

@router.post(
    "/events/ingest",
    response_model=IngestResponse,
    tags=["Producer"],
    summary="Gửi event vào Hub",
    responses={
        200: {
            "description": "Event đã được nhận và đưa vào queue",
            "content": {
                "application/json": {
                    "examples": {
                        "face_recognition": {
                            "summary": "Face Recognition event",
                            "value": {"success": True, "event_id": "550e8400-e29b-41d4-a716-446655440000", "message": "Event queued successfully. topic=security"},
                        },
                        "fingerprint": {
                            "summary": "Fingerprint event",
                            "value": {"success": True, "event_id": "7ba94118-d3f4-4b97-bc30-e81d1f9684b2", "message": "Event queued successfully. topic=security"},
                        },
                    }
                }
            },
        },
        400: {"description": "Lỗi dữ liệu đầu vào"},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "verify_matched": {
                            "summary": "✅ verify_matched — Xác thực thành công",
                            "value": {
                                "source": "face_recognition_api",
                                "type": "face_recognition",
                                "priority": "high",
                                "payload": {
                                    "event":     "verify_matched",
                                    "phase":     "matched",
                                    "matched":   True,
                                    "username":  "nguyen_van_a",
                                    "position":  "Nhan vien",
                                    "score":     0.8542,
                                    "source":    "http://192.168.x.x:8090/stream",
                                    "timestamp": "2026-04-02T10:00:00",
                                    "message":   "✅ Xác thực thành công: nguyen_van_a (0.8542)",
                                    "face_crop_b64": "<base64_jpeg>",
                                },
                            },
                        },
                        "verify_unmatched": {
                            "summary": "❌ verify_unmatched — Có mặt nhưng không khớp",
                            "value": {
                                "source": "face_recognition_api",
                                "type": "face_recognition",
                                "priority": "high",
                                "payload": {
                                    "event":            "verify_unmatched",
                                    "phase":            "scanning",
                                    "matched":          False,
                                    "username":         None,
                                    "nearest":          "nguyen_van_a",
                                    "nearest_position": "Nhan vien",
                                    "score":            0.3210,
                                    "source":           "http://192.168.x.x:8090/stream",
                                    "timestamp":        "2026-04-02T10:00:01",
                                    "message":          "❌ Không nhận diện được — gần nhất: nguyen_van_a (score=0.321)",
                                    "face_crop_b64":    "<base64_jpeg>",
                                },
                            },
                        },
                        "enroll3_angle": {
                            "summary": "📸 enroll3_angle — Chụp 1 góc thành công",
                            "value": {
                                "source": "face_recognition_api",
                                "type": "face_recognition",
                                "priority": "high",
                                "payload": {
                                    "event":          "enroll3_angle",
                                    "step":           1,
                                    "total_steps":    3,
                                    "required_angle": "THANG",
                                    "captured":       "THANG",
                                    "username":       "nguyen_van_a",
                                    "source":         "0",
                                    "timestamp":      "2026-04-02T10:00:00",
                                    "message":        "✅ Đã chụp góc THANG cho 'nguyen_van_a'!",
                                    "face_crop_b64":  "<base64_jpeg>",
                                },
                            },
                        },
                        "enroll3_done": {
                            "summary": "🎉 enroll3_done — Hoàn thành đăng ký 3 góc",
                            "value": {
                                "source": "face_recognition_api",
                                "type": "face_recognition",
                                "priority": "high",
                                "payload": {
                                    "event":           "enroll3_done",
                                    "done":            True,
                                    "username":        "nguyen_van_a",
                                    "angles_captured": ["THANG", "TRAI", "PHAI"],
                                    "source":          "0",
                                    "timestamp":       "2026-04-02T10:00:05",
                                    "message":         "✅ Đăng ký thành công 3 góc cho 'nguyen_van_a'!",
                                },
                            },
                        },
                        "fingerprint": {
                            "summary": "🖐 Fingerprint",
                            "value": {
                                "source": "fingerprint_reader_01",
                                "type": "fingerprint",
                                "priority": "high",
                                "payload": {
                                    "person_id":   "EMP001",
                                    "person_name": "Nguyen Van A",
                                    "finger_id":   3,
                                    "confidence":  0.99,
                                    "action":      "entry",
                                    "location":    "main_entrance",
                                    "reader_id":   "FP-001",
                                },
                            },
                        },
                        "card_reader": {
                            "summary": "💳 Card Reader (thẻ từ)",
                            "value": {
                                "source": "card_reader_01",
                                "type": "card_reader",
                                "priority": "high",
                                "payload": {
                                    "card_id":     "A1B2C3D4",
                                    "person_id":   "EMP001",
                                    "person_name": "Nguyen Van A",
                                    "action":      "entry",
                                    "location":    "main_entrance",
                                    "reader_id":   "CR-001",
                                    "access":      True,
                                    "message":     "Quẹt thẻ thành công",
                                },
                            },
                        },
                    }
                }
            }
        }
    },
)
async def ingest_event(raw: RawEvent):
    """
    Gửi event vào Hub qua REST HTTP — thay thế cho WebSocket Producer.

    **Hữu ích cho:** Script, cron job, hệ thống không hỗ trợ WebSocket.

    **Type → Topic mapping tự động:**
    - `face_recognition` → `security`
    - `fingerprint` → `security`

    **Priority:** `low` | `medium` *(default)* | `high` | `urgent`

    **face_crop_base64:** Nếu payload chứa `face_crop_base64`, Hub tự lưu thành file
    vào `/mnt/faces/` và thay bằng `face_image_url` + `face_image_path`.
    """
    try:
        # Intercept face_crop_base64: lưu file, thay bằng URL
        if isinstance(raw.payload, dict) and "face_crop_base64" in raw.payload:
            base64_str = raw.payload.pop("face_crop_base64")
            file_path  = save_face_crop(base64_str, prefix="face")
            raw.payload["face_image_url"]  = get_image_url(file_path, get_hub_base_url())
            raw.payload["face_image_path"] = file_path

        normalized = normalizer.normalize(raw)
        queued = await event_bus.publish(normalized)

        logger.info(
            "REST ingest: id=%s type=%s topic=%s source=%s queued=%s",
            normalized.id, normalized.type, normalized.topic, normalized.source, queued,
        )

        return IngestResponse(
            success  = True,
            event_id = str(normalized.id),
            message  = f"Event queued successfully. topic={normalized.topic}" if queued
                       else "Queue full, event dropped.",
        )
    except Exception as exc:
        logger.exception("Ingest error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /events/recent - Query lịch sử events (in-memory + MongoDB fallback)
# ---------------------------------------------------------------------------

@router.get("/events/recent", response_model=RecentEventsResponse, tags=["Consumer"])
async def get_recent_events(
    topic: str  = Query(default="*", description="Topic cần query, dùng '*' để lấy tất cả"),
    limit: int  = Query(default=50, ge=1, le=500, description="Số lượng events tối đa"),
    from_db: bool = Query(default=False, description="True = query từ MongoDB (lịch sử lâu dài), False = in-memory"),
):
    """
    Lấy N sự kiện gần nhất của một topic.
    - `from_db=false` (mặc định): từ in-memory (nhanh, tối đa 100 event/topic)
    - `from_db=true`: từ MongoDB (lịch sử đầy đủ, chậm hơn)
    """
    if from_db:
        try:
            from core.database import EventDocument
            from beanie.operators import In
            query = {}
            if topic != "*":
                docs = await EventDocument.find(
                    EventDocument.topic == topic
                ).sort(-EventDocument.timestamp).limit(limit).to_list()
            else:
                docs = await EventDocument.find_all().sort(
                    -EventDocument.timestamp
                ).limit(limit).to_list()

            # Chuyển EventDocument → NormalizedEvent để response_model hoạt động
            from core.models import NormalizedEvent, EventMetadata
            from uuid import UUID
            events = []
            for d in reversed(docs):  # reversed để mới nhất ở cuối
                try:
                    events.append(NormalizedEvent(
                        id        = UUID(d.event_id),
                        timestamp = d.timestamp,
                        source    = d.source,
                        type      = d.type,
                        topic     = d.topic,
                        priority  = d.priority,
                        payload   = d.payload,
                        metadata  = EventMetadata(**d.metadata) if d.metadata else EventMetadata(),
                    ))
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("MongoDB query failed, fallback to in-memory: %s", exc)
            events = event_bus.get_history(topic=topic, limit=limit)
    else:
        events = event_bus.get_history(topic=topic, limit=limit)

    return RecentEventsResponse(
        topic  = topic,
        count  = len(events),
        events = events,
    )


# ---------------------------------------------------------------------------
# GET /events/topics - Danh sách topics đang active
# ---------------------------------------------------------------------------

@router.get("/events/topics", response_model=TopicsResponse, tags=["Consumer"])
async def get_active_topics():
    """
    Trả về danh sách topics đang có events trong lịch sử.
    Consumer dùng để biết mình có thể subscribe topic nào.
    """
    return TopicsResponse(
        topics          = event_bus.get_active_topics(),
        total_consumers = event_bus.get_total_consumers(),
    )


# ---------------------------------------------------------------------------
# GET /health - Health check
# ---------------------------------------------------------------------------

@router.get("/health", tags=["System"])
async def health_check():
    """Health check + thống kê hệ thống"""
    # Kiểm tra MongoDB
    mongo_status = "unknown"
    mongo_total  = 0
    try:
        from core.database import EventDocument
        mongo_total  = await EventDocument.count()
        mongo_status = "ok"
    except Exception as exc:
        mongo_status = f"error: {exc}"

    return {
        "status"          : "ok",
        "queue_size"      : event_bus.get_queue_size(),
        "active_topics"   : event_bus.get_active_topics(),
        "total_consumers" : event_bus.get_total_consumers(),
        "mongodb"         : {"status": mongo_status, "total_events": mongo_total},
    }


# ---------------------------------------------------------------------------
# DELETE /events - Xóa toàn bộ events (in-memory + MongoDB)
# ---------------------------------------------------------------------------

@router.delete(
    "/events",
    tags=["System"],
    summary="Xóa toàn bộ events",
    description=(
        "Xóa **tất cả** events khỏi:\n"
        "- In-memory history (EventBus)\n"
        "- MongoDB collection\n\n"
        "⚠️ Không thể hoàn tác. Frontend sẽ hiển thị 0 events sau khi clear và reconnect."
    ),
    responses={
        200: {
            "description": "Xóa thành công",
            "content": {
                "application/json": {
                    "example": {"message": "Đã xóa tất cả events", "deleted_mongo": 46, "cleared_memory": True}
                }
            },
        }
    },
)
async def delete_all_events():
    """Xóa toàn bộ events: in-memory history + MongoDB"""
    # 1. Xóa in-memory history
    event_bus.clear_history()

    # 2. Xóa MongoDB
    deleted_count = 0
    try:
        from core.database import EventDocument
        result = await EventDocument.find_all().delete()
        deleted_count = result.deleted_count if result else 0
    except Exception as exc:
        logger.warning("MongoDB delete all events failed: %s", exc)

    return {
        "message"       : "Đã xóa tất cả events",
        "deleted_mongo" : deleted_count,
        "cleared_memory": True,
    }


# ---------------------------------------------------------------------------
# GET /events/types - Danh sách event types hỗ trợ
# ---------------------------------------------------------------------------

@router.get("/events/types", tags=["System"])
async def get_event_types():
    """Trả về danh sách event types và topics được hỗ trợ"""
    from core.models import EventType, EventTopic, EventPriority, TYPE_TO_TOPIC
    return {
        "event_types" : [e.value for e in EventType],
        "topics"      : [e.value for e in EventTopic],
        "priorities"  : [e.value for e in EventPriority],
        "type_to_topic_mapping": {
            et.value: topic.value for et, topic in TYPE_TO_TOPIC.items()
        },
    }
