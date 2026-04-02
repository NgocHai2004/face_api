"""
WebSocket endpoint cho Producers.
URL: ws://host:8000/ws/producer
     ws://host:8000/ws/producer?source=camera_01   (source override)

Hỗ trợ 2 format:

Format 1 - Chuẩn (có payload wrapper):
{
  "source": "camera_01",
  "type": "face_recognition",
  "priority": "high",
  "payload": { "username": "long", "face_crop_b64": "<base64>" }
}

Format 2 - Flat (không có payload wrapper, data phẳng):
{
  "event": "verify3_angle",
  "source": "0",
  "matched": true,
  "username": "long",
  "score": 0.843,
  "face_crop_b64": "<base64>",
  "timestamp": "2026-03-29T14:57:34.686356"
}

Trong cả 2 format:
- face_crop_b64 / face_crop_base64 → Hub lưu thành file /mnt/faces/, thay bằng face_image_url
- event → map sang type nếu không có type
"""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.event_bus import event_bus
from core.image_store import save_face_crop, get_image_url, get_hub_base_url
from core.normalizer import EventNormalizer

logger = logging.getLogger("event_hub.ws_producer")
router = APIRouter()
normalizer = EventNormalizer()

# Fields chứa base64 ảnh cần intercept: (field_name, output_url_key, output_path_key, file_prefix)
_BASE64_FIELDS = (
    ("face_crop_b64",    "face_image_url",  "face_image_path",  "face"),
    ("face_crop_base64", "face_image_url",  "face_image_path",  "face"),
    ("face_db_64",       "face_db_url",     "face_db_path",     "db"),
)

# Fields thuộc về RawEvent schema (không đưa vào payload)
_SCHEMA_FIELDS = {"source", "type", "priority", "topic", "payload"}


def _normalize_data(data: dict, client_host: str, source_override: str | None) -> dict:
    """
    Chuẩn hóa dict thô từ producer thành format RawEvent hợp lệ.

    - Map field 'event' → 'type' nếu chưa có 'type'
    - Nếu flat JSON → gom tất cả fields thừa vào 'payload'
    - Intercept face_crop_b64/base64 → lưu file → thay bằng face_image_url
    - Override source nếu cần
    """
    # Map 'event' → 'type'
    if "type" not in data and "event" in data:
        data["type"] = data.pop("event")

    # Xử lý source
    if source_override:
        data["source"] = source_override
    elif not data.get("source"):
        data["source"] = f"unknown_{client_host}"

    # Nếu flat JSON (không có payload key) → gom fields thừa vào payload
    if "payload" not in data:
        payload = {}
        extra_keys = [k for k in list(data.keys()) if k not in _SCHEMA_FIELDS]
        for k in extra_keys:
            payload[k] = data.pop(k)
        data["payload"] = payload

    # Intercept base64 ở root level
    for field, url_key, path_key, prefix in _BASE64_FIELDS:
        if field in data:
            b64 = data.pop(field)
            file_path = save_face_crop(b64, prefix=prefix)
            data.setdefault("payload", {})
            data["payload"][url_key]  = get_image_url(file_path, get_hub_base_url())
            data["payload"][path_key] = file_path

    # Intercept base64 trong payload
    payload = data.get("payload", {})
    if isinstance(payload, dict):
        for field, url_key, path_key, prefix in _BASE64_FIELDS:
            if field in payload:
                b64 = payload.pop(field)
                file_path = save_face_crop(b64, prefix=prefix)
                payload[url_key]  = get_image_url(file_path, get_hub_base_url())
                payload[path_key] = file_path
        data["payload"] = payload

    return data


@router.websocket("/ws/producer")
async def producer_ws(websocket: WebSocket, source: str | None = None):
    """
    WebSocket endpoint dành cho producers.
    - Nhận JSON event (flat hoặc có payload wrapper), normalize, đưa vào EventBus.
    - `source` query param để override source của thiết bị.
    - face_crop_b64/face_crop_base64 → lưu file /mnt/faces/ → thay bằng face_image_url.
    """
    await websocket.accept()
    client = websocket.client
    logger.info("Producer connected: %s source_override=%s", client, source)

    try:
        while True:
            raw_text = await websocket.receive_text()

            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "status": "error",
                    "message": "Invalid JSON format",
                })
                continue

            # Kiểm tra có 'type' hoặc 'event'
            if "type" not in data and "event" not in data:
                await websocket.send_json({
                    "status": "error",
                    "message": "Missing required field: 'type' or 'event'",
                })
                continue

            try:
                data = _normalize_data(
                    data,
                    client_host=client.host if client else "device",
                    source_override=source,
                )

                normalized = normalizer.normalize_dict(data)
                queued = await event_bus.publish(normalized)

                await websocket.send_json({
                    "status": "ok",
                    "event_id": str(normalized.id),
                    "queued": queued,
                    "topic": normalized.topic,
                    "type": normalized.type,
                })
                logger.info(
                    "Event received: id=%s type=%s topic=%s source=%s",
                    normalized.id, normalized.type, normalized.topic, normalized.source,
                )
            except Exception as exc:
                logger.exception("Normalization error: %s", exc)
                await websocket.send_json({
                    "status": "error",
                    "message": f"Normalization failed: {str(exc)}",
                })

    except WebSocketDisconnect:
        logger.info("Producer disconnected: %s", client)
    except Exception as exc:
        logger.exception("Producer WS error: %s", exc)
