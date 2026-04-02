from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
import cv2
import asyncio
import base64
import numpy as np
import json
import time
import threading
from typing import Optional
from datetime import datetime

from app.database import User
from app.face_utils import (
    extract_embedding_from_image,
    bytes_to_embedding, verify_faces, crop_to_base64,
)
from app.fast_detector import get_fast_detector, annotate_faces
from app.camera_manager import camera_manager
from app.config import settings
from app.ws_producer import push_event_async
from app.rtsp_utils import _parse_source

router = APIRouter()

# ── Active SSE sessions {session_id: should_stop} ────────────
_sessions: dict[str, bool] = {}

# Số frame bỏ qua giữa 2 lần gọi InsightFace embedding
# 15fps SSE / EMBED_EVERY=8 → embedding ~1.8 lần/giây
EMBED_EVERY = 8


def _annotate_frame(frame: np.ndarray, label: str, matched: bool, score: float) -> np.ndarray:
    h, w = frame.shape[:2]
    color = (0, 255, 0) if matched else (0, 0, 255)
    cv2.rectangle(frame, (4, 4), (w - 4, h - 4), color, 2)
    cv2.putText(frame, f"{label}  {score:.2f}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    return frame


def _get_frame_from_source(src, rtsp_cap) -> Optional[np.ndarray]:
    if rtsp_cap is not None:
        ret, f = rtsp_cap.read()
        return f if ret and f is not None else None
    frame = camera_manager.get_frame()
    return frame


# ─────────────────────────────────────────────────────────────
# SSE stream – 15 fps detect (YuNet) + 1-2 fps embed (InsightFace bg)
# ─────────────────────────────────────────────────────────────
async def _stream_generator(rtsp_url: str, username: Optional[str], session_id: str):
    _sessions[session_id] = False
    interval = 1.0 / 15  # target 15 fps

    # Preload users + stored embeddings once từ MongoDB
    if username:
        users = await User.find(
            User.username == username,
            User.face_embedding_b64 != None,  # noqa: E711
        ).to_list()
    else:
        users = await User.find(
            User.face_embedding_b64 != None  # noqa: E711
        ).to_list()

    stored_embeddings = [
        (u.username, bytes_to_embedding(u.face_embedding)) for u in users
    ]

    try:
        src = _parse_source(rtsp_url)

        # Open camera / RTSP
        rtsp_cap = None
        if isinstance(src, str):
            rtsp_cap = cv2.VideoCapture(src)
            if not rtsp_cap.isOpened():
                yield f"data: {json.dumps({'error': f'Cannot open: {rtsp_url}'})}\n\n"
                return
        else:
            if not camera_manager.is_running or camera_manager._source != src:
                if not camera_manager.start(src):
                    yield f"data: {json.dumps({'error': f'Cannot open camera {src}'})}\n\n"
                    return

        # ── Shared state giữa main loop & InsightFace bg thread ──
        _lock   = threading.Lock()
        _ai_result = {
            "face_crop": None, "matched": False,
            "name": None, "score": 0.0,
            "timestamp": datetime.now().isoformat(),
        }
        _ai_running  = threading.Event()
        _ai_running.set()
        _frame_box: list = [None]   # [frame_for_ai] — None = thread rảnh

        def _ai_worker():
            while _ai_running.is_set():
                frame_copy = _frame_box[0]
                if frame_copy is None:
                    time.sleep(0.01)
                    continue
                _frame_box[0] = None   # đánh dấu đang xử lý

                emb, crop = extract_embedding_from_image(frame_copy)
                name, score, matched = None, 0.0, False
                if emb is not None and stored_embeddings:
                    for uname, stored_emb in stored_embeddings:
                        _, s = verify_faces(stored_emb, emb)
                        if s > score:
                            score = s
                            name = uname
                    matched = score >= settings.FACE_THRESHOLD

                with _lock:
                    _ai_result.update({
                        "face_crop": crop, "matched": matched,
                        "name": name, "score": score,
                        "timestamp": datetime.now().isoformat(),
                    })

        ai_thread = threading.Thread(target=_ai_worker, daemon=True)
        ai_thread.start()

        # YuNet detector (fast, CPU ~5-10ms)
        detector = get_fast_detector()

        frame_counter = 0

        while not _sessions.get(session_id, True):
            t_start = time.monotonic()

            # ── Lấy frame ─────────────────────────────────────
            if rtsp_cap is not None:
                ret, frame = rtsp_cap.read()
                if not ret or frame is None:
                    rtsp_cap.release()
                    rtsp_cap = cv2.VideoCapture(src)
                    ret, frame = rtsp_cap.read()
                    if not ret or frame is None:
                        yield f"data: {json.dumps({'error': 'Lost RTSP connection'})}\n\n"
                        break
            else:
                frame = camera_manager.get_frame()
                if frame is None:
                    await asyncio.sleep(0.02)
                    continue

            frame_counter += 1

            # ── YuNet detect mỗi frame (nhanh ~5-10ms) ────────
            faces = detector.detect(frame)

            # ── Gửi frame cho InsightFace bg thread mỗi EMBED_EVERY frame ──
            if frame_counter % EMBED_EVERY == 0 and _frame_box[0] is None:
                _frame_box[0] = frame.copy()

            # ── Đọc kết quả AI mới nhất (không chờ) ──────────
            with _lock:
                snap = dict(_ai_result)

            # ── Vẽ bbox từ YuNet + label từ InsightFace ───────
            label   = snap["name"] if snap["matched"] else ("Detecting..." if faces else "No Face")
            out     = annotate_faces(frame.copy(), faces, label, snap["matched"], snap["score"])

            # ── Resize + encode ────────────────────────────────
            h_orig, w_orig = out.shape[:2]
            if w_orig > 640:
                out = cv2.resize(out, (640, int(h_orig * 640 / w_orig)))

            _, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 60])
            frame_b64 = base64.b64encode(buf.tobytes()).decode()
            crop_b64  = crop_to_base64(snap["face_crop"]) if snap["face_crop"] is not None else None

            # Hướng mặt từ YuNet (không cần InsightFace)
            direction = faces[0]["direction"] if faces else None

            event_data = {
                "frame":            frame_b64,
                "username":         snap["name"],
                "matched":          snap["matched"],
                "similarity":       round(snap["score"], 4),
                "timestamp":        snap["timestamp"],
                "rtsp_url":         rtsp_url,
                "face_crop_base64": crop_b64,
                "direction":        direction,
                "face_count":       len(faces),
                "message":          f"Nhận diện: {snap['name']}" if snap["matched"] else "Không nhận ra",
            }
            yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"

            if snap["matched"]:
                asyncio.ensure_future(push_event_async(event_data))

            elapsed = time.monotonic() - t_start
            await asyncio.sleep(max(0.0, interval - elapsed))

        # Dọn dẹp
        _ai_running.clear()
        _frame_box[0] = None
        if rtsp_cap:
            rtsp_cap.release()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    finally:
        _sessions.pop(session_id, None)
        yield f"data: {json.dumps({'stopped': True})}\n\n"


@router.get("/stream", summary="SSE stream nhận diện 15fps (YuNet detect + InsightFace embed bg)")
async def stream(
    rtsp_url: str = Query(...),
    username: Optional[str] = Query(None),
    session_id: str = Query(...),
):
    return StreamingResponse(
        _stream_generator(rtsp_url, username, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/stream/stop", summary="Dừng SSE stream")
def stream_stop(session_id: str = Query(...)):
    if session_id in _sessions:
        _sessions[session_id] = True
        return {"stopped": True, "session_id": session_id}
    return {"stopped": False, "session_id": session_id, "detail": "Session not found"}


# /webcam đã được tách sang project webcam_server (port 8090)
# Xem http://192.168.21.47:8090/stream để xem video trực tiếp
