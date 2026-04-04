"""
app/routers/verify3.py

SSE endpoint xác thực khuôn mặt liên tục:
  GET /verify3?source=0[&username=alice]

Flow:
  - Mỗi 1 giây: chụp frame → crop zone giữa → InsightFace trên crop
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
import time
import functools
from typing import Optional
from datetime import datetime

from app.database import User
from app.enroll_state import enroll_state
from app.face_utils import (
    embedding_from_faces,
    bytes_to_embedding, verify_faces,
)
from app.rtsp_utils import _parse_source, fetch_snapshot_from_url
from app.config import settings
from app.ws_producer import push_event_async

router = APIRouter()

MATCH_THRESHOLD   = 0.45  # score tối thiểu để xác thực thành công
SCAN_INTERVAL     = 0.5   # giây giữa mỗi lần quét
PUSH_COOLDOWN_SEC = 10.0  # giây chờ giữa 2 lần push socket cho cùng 1 user

# Zone nhận diện: crop vùng 200×300 ở giữa frame rồi pass InsightFace
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


def _detect_and_match(frame, users, model):
    """
    2-stage pipeline:
      Stage 1 — Crop zone giữa (0ms)
      Stage 2 — InsightFace embed + DB search trên crop (~200-400ms)

    Trả về (best_user, best_score, face_crop_b64) hoặc (None, 0.0, None).
    """
    t_pipeline_start = time.perf_counter()

    # ── Stage 1: Crop zone giữa ────────────────────────────────
    frame_h, frame_w = frame.shape[:2]
    zx1 = int((frame_w - ZONE_W) / 2)
    zy1 = int((frame_h - ZONE_H) / 2)
    zx2 = zx1 + ZONE_W
    zy2 = zy1 + ZONE_H
    # Clamp để tránh out-of-bounds
    zx1 = max(0, zx1); zy1 = max(0, zy1)
    zx2 = min(frame_w, zx2); zy2 = min(frame_h, zy2)
    crop = frame[zy1:zy2, zx1:zx2]
    print(f"[CROP] Zone ({zx1},{zy1})-({zx2},{zy2}) → crop shape={crop.shape}", flush=True)

    # ── Stage 2: InsightFace inference trên crop ──────────────
    t_reco_start = time.perf_counter()
    faces = model.get(crop)
    t_reco_ms = (time.perf_counter() - t_reco_start) * 1000
    print(
        f"[DETECT] InsightFace inference {t_reco_ms:.1f}ms"
        f" — detected {len(faces) if faces else 0} face(s) in crop",
        flush=True,
    )
    if not faces:
        return None, 0.0, None

    # Chọn mặt lớn nhất trong crop
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    # Lấy embedding từ crop
    embedding, face_crop = embedding_from_faces(crop, [face])
    if embedding is None:
        return None, 0.0, None

    # ── DB search (RAM) ────────────────────────────────────────
    t_search_start = time.perf_counter()
    best_score = 0.0
    best_user  = None
    for u in users:
        stored = bytes_to_embedding(u.face_embedding)
        _, score = verify_faces(stored, embedding, label=u.username)
        if score > best_score:
            best_score = score
            best_user  = u
    t_search_ms = (time.perf_counter() - t_search_start) * 1000
    print(
        f"[SEARCH] {len(users)} user(s) — {t_search_ms:.1f}ms"
        f" — best: user={best_user.username if best_user else 'None'}"
        f" score={best_score:.4f} → {'MATCHED ✅' if best_score >= MATCH_THRESHOLD else 'UNMATCHED ❌'}",
        flush=True,
    )

    # Encode face crop → base64
    face_crop_b64 = None
    if face_crop is not None and face_crop.size > 0:
        _, crop_buf = cv2.imencode(".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        face_crop_b64 = base64.b64encode(crop_buf.tobytes()).decode()

    t_pipeline_ms = (time.perf_counter() - t_pipeline_start) * 1000
    print(f"[VERIFY3] ── Total frame processing: {t_pipeline_ms:.1f}ms ──", flush=True)

    return best_user, round(best_score, 4), face_crop_b64


async def _verify_generator(source: str, username: Optional[str]):
    from app.face_utils import get_face_model

    print(f"[VERIFY3] Generator started — source={source} username={username}", flush=True)
    src = _parse_source(source)
    print(f"[VERIFY3] Parsed source → {src}", flush=True)

    # Cooldown tracker: username -> timestamp lần push cuối
    _last_push: dict[str, float] = {}

    # Load users từ MongoDB
    query = User.find(User.face_embedding_b64 != None)  # noqa: E711
    if username:
        query = User.find(User.username == username, User.face_embedding_b64 != None)  # noqa: E711
    users = await query.to_list()
    print(f"[VERIFY3] Loaded {len(users)} user(s) from MongoDB", flush=True)

    if not users:
        yield _event({"error": "Không có khuôn mặt nào trong DB. Hãy đăng ký trước."})
        return

    get_frame, cap = _get_frame_fn(src)
    if get_frame is None:
        yield _event({"error": f"Không mở được source: {source}"})
        return

    model = get_face_model()
    print(f"[VERIFY3] Model loaded — entering scan loop (interval={SCAN_INTERVAL}s)", flush=True)

    try:
        loop_count = 0
        while True:
            loop_count += 1
            await asyncio.sleep(SCAN_INTERVAL)

            # ── Tạm dừng xác thực khi đang có phiên đăng ký ─────────
            if enroll_state.is_active():
                yield _event({
                    "phase":     "paused",
                    "event":     "verify_paused",
                    "source":    source,
                    "timestamp": datetime.now().isoformat(),
                    "message":   "⏸ Đang đăng ký khuôn mặt — xác thực tạm dừng.",
                })
                continue

            # ── Lấy frame (non-blocking via executor) ────────────────
            t_getframe_start = time.perf_counter()
            loop = asyncio.get_event_loop()
            try:
                frame = await loop.run_in_executor(None, get_frame)
            except Exception as ex:
                print(f"[VERIFY3] get_frame() EXCEPTION: {ex}", flush=True)
                continue
            t_getframe_ms = (time.perf_counter() - t_getframe_start) * 1000
            if frame is None:
                print(f"[VERIFY3] get_frame() None ({t_getframe_ms:.1f}ms) — skipping", flush=True)
                continue
            print(f"[VERIFY3] get_frame() OK {t_getframe_ms:.1f}ms — shape={frame.shape}", flush=True)

            # ── Detect + match trên crop zone (executor) ─────────────
            t_frame_start = time.perf_counter()
            best_user, best_score, face_crop_b64 = await loop.run_in_executor(
                None, functools.partial(_detect_and_match, frame, users, model)
            )
            t_total_ms = (time.perf_counter() - t_frame_start) * 1000

            # ── Vẽ zone lên full frame để preview ────────────────────
            fh, fw = frame.shape[:2]
            zx1 = int((fw - ZONE_W) / 2)
            zy1 = int((fh - ZONE_H) / 2)
            zx2 = zx1 + ZONE_W
            zy2 = zy1 + ZONE_H
            zone_color = (0, 255, 0) if best_user is not None else (0, 165, 255)
            cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), zone_color, 2)
            cv2.putText(frame, "Dung vao day", (zx1, zy1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, zone_color, 1)

            matched = best_score >= MATCH_THRESHOLD
            ts      = datetime.now().isoformat()
            now_ts  = time.monotonic()

            if matched:
                # ✅ Có người + thỏa mãn → luôn yield SSE, nhưng chỉ push socket nếu hết cooldown
                event_data = {
                    "phase":         "matched",
                    "event":         "verify_matched",
                    "type":          "face_recognition",
                    "username":      best_user.username,
                    "score":         best_score,
                    "threshold":     MATCH_THRESHOLD,
                    "source":        source,
                    "timestamp":     ts,
                    "face_crop_b64": face_crop_b64,
                    "matched":       True,
                }
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                event_data["frame_b64"] = base64.b64encode(buf.tobytes()).decode()
                yield _event(event_data)

                last = _last_push.get(best_user.username, 0)
                if now_ts - last >= PUSH_COOLDOWN_SEC:
                    _last_push[best_user.username] = now_ts
                    socket_data = {k: v for k, v in event_data.items() if k != "frame_b64"}
                    asyncio.ensure_future(push_event_async(socket_data, event_type="face_recognition"))

            elif best_user is not None:
                # ❌ Có mặt nhưng score thấp
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                event_data = {
                    "phase":         "unmatched",
                    "event":         "verify_unmatched",
                    "type":          "face_recognition",
                    "username":      None,
                    "score":         best_score,
                    "threshold":     MATCH_THRESHOLD,
                    "source":        source,
                    "timestamp":     ts,
                    "frame_b64":     base64.b64encode(buf.tobytes()).decode(),
                    "face_crop_b64": face_crop_b64,
                }
                yield _event(event_data)
                socket_data = {k: v for k, v in event_data.items() if k != "frame_b64"}
                asyncio.ensure_future(push_event_async(socket_data, event_type="face_recognition"))

            else:
                # Không có mặt trong crop → bắn no_face
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                yield _event({
                    "phase":     "no_face",
                    "event":     "verify_no_face",
                    "source":    source,
                    "timestamp": ts,
                    "frame_b64": base64.b64encode(buf.tobytes()).decode(),
                    "message":   "Không phát hiện khuôn mặt trong vùng nhận diện",
                })

    except asyncio.CancelledError:
        print("[VERIFY3] Generator cancelled (client disconnected)", flush=True)
    finally:
        if cap is not None:
            cap.release()


@router.get(
    "/verify3",
    summary="SSE xác thực khuôn mặt liên tục — crop zone giữa → InsightFace",
    description=(
        "Stream SSE xác thực mặt mỗi giây.\n\n"
        "**Pipeline:** crop zone 200×300 giữa frame → InsightFace embed → cosine search DB\n\n"
        "**Events:**\n"
        "- `verify_matched`   – khớp (score ≥ threshold)\n"
        "- `verify_unmatched` – có mặt nhưng score thấp\n"
        "- `verify_no_face`   – không phát hiện mặt trong zone\n"
        "- `verify_paused`    – đang có phiên đăng ký\n\n"
        "**source:** `0` = webcam local, `http://...` = MJPEG/snapshot, `rtsp://...` = RTSP stream"
    ),
)
async def verify3(
    source: str = Query("0", description="Nguồn video: 0/1 (camera), http://..., rtsp://..."),
    username: Optional[str] = Query(None, description="Chỉ so sánh với user cụ thể"),
):
    return StreamingResponse(
        _verify_generator(source, username),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
