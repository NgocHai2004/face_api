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
                        "face_recognition": {
                            "summary": "👤 Face Recognition (matched)",
                            "value": {
                                "source": "face_recognition_api",
                                "type": "face_recognition",
                                "priority": "high",
                                "payload": {
                                    "username":         "Nguyen Van A",
                                    "matched":          True,
                                    "similarity":       0.97,
                                    "face_crop_base64": "<base64_jpeg_string>",
                                    "timestamp":        "2026-03-29T10:54:17.445206",
                                    "rtsp_url":         "rtsp://camera_ip/stream",
                                    "message":          "Nhận diện thành công",
                                },
                            },
                        },
                        "face_recognition_not_matched": {
                            "summary": "👤 Face Not Detected",
                            "value": {
                                "source": "face_recognition_api",
                                "type": "face_recognition",
                                "priority": "medium",
                                "payload": {
                                    "username":         None,
                                    "matched":          False,
                                    "similarity":       0,
                                    "face_crop_base64": None,
                                    "timestamp":        "2026-03-29T10:54:17.445206",
                                    "rtsp_url":         "0",
                                    "message":          "Không phát hiện khuôn mặt trong frame RTSP",
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
# GET /events/recent - Query lịch sử events
# ---------------------------------------------------------------------------

@router.get("/events/recent", response_model=RecentEventsResponse, tags=["Consumer"])
async def get_recent_events(
    topic: str = Query(default="*", description="Topic cần query, dùng '*' để lấy tất cả"),
    limit: int = Query(default=50, ge=1, le=500, description="Số lượng events tối đa"),
):
    """
    Lấy N sự kiện gần nhất của một topic.
    Hữu ích cho consumer muốn poll thay vì WebSocket.
    """
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
    return {
        "status"          : "ok",
        "queue_size"      : event_bus.get_queue_size(),
        "active_topics"   : event_bus.get_active_topics(),
        "total_consumers" : event_bus.get_total_consumers(),
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
