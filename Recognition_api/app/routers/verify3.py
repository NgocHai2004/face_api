"""
app/routers/verify3.py

SSE endpoint xác thực khuôn mặt liên tục:
  GET /verify3?source=0[&username=alice]

Flow:
  - Mỗi 1 giây: chụp frame → detect mặt
  - Có người + score >= threshold  → bắn verify_matched   + push socket
  - Có người + score <  threshold  → bắn verify_unmatched + push socket
  - Không có người (faces=[])      → bắn verify_no_face   (không push socket)
"""
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
import cv2
import asyncio
import base64
import json
from typing import Optional
from datetime import datetime

from app.database import User
from app.face_utils import (
    embedding_from_faces,
    bytes_to_embedding, verify_faces,
)
from app.rtsp_utils import _parse_source, fetch_snapshot_from_url
from app.config import settings
from app.ws_producer import push_event_async

router = APIRouter()

MATCH_THRESHOLD   = 0.6   # score tối thiểu để xác thực thành công
SCAN_INTERVAL     = 1.0   # giây giữa mỗi lần quét
PUSH_COOLDOWN_SEC = 10.0  # giây chờ giữa 2 lần push socket cho cùng 1 user

# Zone nhận diện: chỉ xử lý mặt có tâm nằm trong vùng 200×300 ở giữa frame
ZONE_W = 200
ZONE_H = 300


def _event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_frame_fn(src):
    """Trả về hàm lấy frame phù hợp với loại source."""
    if isinstance(src, int):
        from app.camera_manager import camera_manager
        if not camera_manager.is_running or camera_manager._source != src:
            camera_manager.start(src)
        return camera_manager.get_frame, None

    if isinstance(src, str) and src.lower().startswith("http"):
        snapshot_url = src.replace("/stream", "/snapshot")
        return lambda: fetch_snapshot_from_url(snapshot_url), None

    # RTSP
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None, None
    return lambda: (lambda ret, f: f if ret else None)(*cap.read()), cap


def _face_in_zone(face, frame_h: int, frame_w: int) -> bool:
    """Kiểm tra tâm bbox của face có nằm trong zone 300×400 giữa frame không."""
    x1, y1, x2, y2 = face.bbox[:4]
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    zone_x1 = (frame_w - ZONE_W) / 2
    zone_y1 = (frame_h - ZONE_H) / 2
    zone_x2 = zone_x1 + ZONE_W
    zone_y2 = zone_y1 + ZONE_H
    return zone_x1 <= cx <= zone_x2 and zone_y1 <= cy <= zone_y2


def _detect_and_match(frame, users, model):
    """
    Detect mặt trong frame → chỉ xử lý mặt trong zone giữa → so sánh với tất cả users.
    Trả về (best_user, best_score, face_crop_b64) hoặc (None, 0.0, None).
    """
    faces = model.get(frame)
    if not faces:
        return None, 0.0, None

    frame_h, frame_w = frame.shape[:2]

    # Lọc chỉ giữ mặt có tâm trong zone
    faces = [f for f in faces if _face_in_zone(f, frame_h, frame_w)]
    if not faces:
        return None, 0.0, None

    # Chọn mặt lớn nhất trong zone
    face = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))

    # Lấy embedding
    embedding, face_crop = embedding_from_faces(frame, [face])
    if embedding is None:
        return None, 0.0, None

    # So sánh với DB
    best_score = 0.0
    best_user  = None
    for u in users:
        stored = bytes_to_embedding(u.face_embedding)
        _, score = verify_faces(stored, embedding)
        if score > best_score:
            best_score = score
            best_user  = u

    # Crop face base64
    face_crop_b64 = None
    if face_crop is not None and face_crop.size > 0:
        _, crop_buf = cv2.imencode(".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        face_crop_b64 = base64.b64encode(crop_buf.tobytes()).decode()

    return best_user, round(best_score, 4), face_crop_b64


async def _verify_generator(source: str, username: Optional[str]):
    from app.face_utils import get_face_model

    src = _parse_source(source)
    # Cooldown tracker: username -> timestamp lần push cuối
    _last_push: dict[str, float] = {}

    # Load users từ MongoDB
    query = User.find(User.face_embedding_b64 != None)  # noqa: E711
    if username:
        query = User.find(User.username == username, User.face_embedding_b64 != None)  # noqa: E711
    users = await query.to_list()

    if not users:
        yield _event({"error": "Không có khuôn mặt nào trong DB. Hãy đăng ký trước."})
        return

    get_frame, cap = _get_frame_fn(src)
    if get_frame is None:
        yield _event({"error": f"Không mở được source: {source}"})
        return

    model = get_face_model()

    try:
        while True:
            await asyncio.sleep(SCAN_INTERVAL)

            frame = get_frame()
            if frame is None:
                continue  # không có frame → im lặng, thử lại

            # ── Detect + match ───────────────────────────────────────
            best_user, best_score, face_crop_b64 = _detect_and_match(frame, users, model)

            # Vẽ zone lên frame để hiển thị
            fh, fw = frame.shape[:2]
            zx1 = int((fw - ZONE_W) / 2)
            zy1 = int((fh - ZONE_H) / 2)
            zx2 = zx1 + ZONE_W
            zy2 = zy1 + ZONE_H
            zone_color = (0, 255, 0) if best_user is not None else (0, 165, 255)
            cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), zone_color, 2)
            cv2.putText(frame, "Dung vao day", (zx1, zy1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, zone_color, 1)

            # Không có mặt trong frame → bắn no_face, tiếp tục
            if best_user is None and best_score == 0.0:
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                yield _event({
                    "phase":     "no_face",
                    "event":     "verify_no_face",
                    "source":    source,
                    "timestamp": datetime.now().isoformat(),
                    "frame_b64": base64.b64encode(buf.tobytes()).decode(),
                    "message":   "Không phát hiện khuôn mặt",
                })
                continue

            # Encode preview frame (chỉ khi cần gửi SSE)
            _, buf    = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
            frame_b64 = base64.b64encode(buf.tobytes()).decode()

            matched   = best_score >= MATCH_THRESHOLD
            ts        = datetime.now().isoformat()

            import time
            now_ts = time.monotonic()

            if matched:
                # ✅ Có người + thỏa mãn → luôn yield SSE, nhưng chỉ push socket nếu hết cooldown
                event_data = {
                    "phase":         "matched",
                    "event":         "verify_matched",
                    "type":          "face_recognition",
                    "username":      best_user.username,
                    "position":      best_user.position or "",
                    "expiry_date":   best_user.expiry_date.isoformat() if best_user.expiry_date else None,
                    "score":         best_score,
                    "source":        source,
                    "timestamp":     ts,
                    "face_crop_b64": face_crop_b64,
                    "matched":       True,
                    "message":       f"✅ Xác thực thành công: {best_user.username} ({best_score})",
                }
                yield _event({**event_data, "frame_b64": frame_b64})

                # Push socket chỉ 1 lần mỗi PUSH_COOLDOWN_SEC giây / user
                last = _last_push.get(best_user.username, 0.0)
                if now_ts - last >= PUSH_COOLDOWN_SEC:
                    _last_push[best_user.username] = now_ts
                    asyncio.ensure_future(push_event_async(event_data))

            else:
                # ❌ Có người + không thỏa mãn → yield SSE + push socket (có cooldown)
                event_data = {
                    "phase":            "scanning",
                    "event":            "verify_unmatched",
                    "type":             "face_recognition",
                    "username":         None,
                    "nearest":          best_user.username,
                    "nearest_position": best_user.position or "",
                    "nearest_expiry_date": best_user.expiry_date.isoformat() if best_user.expiry_date else None,
                    "score":            best_score,
                    "source":           source,
                    "timestamp":        ts,
                    "face_crop_b64":    face_crop_b64,
                    "matched":          False,
                    "message":          f"❌ Không nhận diện được — gần nhất: {best_user.username} (score={best_score})",
                }
                yield _event({**event_data, "frame_b64": frame_b64})

                # Push socket với cooldown riêng cho unmatched
                last_unmatched = _last_push.get("__unmatched__", 0.0)
                if now_ts - last_unmatched >= PUSH_COOLDOWN_SEC:
                    _last_push["__unmatched__"] = now_ts
                    asyncio.ensure_future(push_event_async(event_data))

    except asyncio.CancelledError:
        pass
    except Exception as e:
        yield _event({"error": str(e)})
    finally:
        if cap is not None:
            cap.release()


@router.get("/verify3", summary="Xác thực khuôn mặt liên tục — 1s/lần (SSE)")
async def verify_continuous(
    source: str = Query(default="0"),
    username: Optional[str] = Query(None),
):
    return StreamingResponse(
        _verify_generator(source, username),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
