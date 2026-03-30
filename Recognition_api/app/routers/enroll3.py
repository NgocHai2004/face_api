"""
app/routers/enroll3.py

SSE endpoint đăng ký khuôn mặt 3 góc:
  GET /enroll3?username=alice&source=0

Flow: THANG → TRAI → PHAI
  Phát hiện đúng hướng → chụp 1 ảnh ngay → chuyển góc tiếp theo
  Cuối: lưu embedding trung bình vào DB
"""
from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse
import cv2
import asyncio
import base64
import numpy as np
import json
from typing import Optional
from sqlalchemy.orm import Session

from app.database import get_db, User
from app.face_utils import (
    extract_embedding_from_image,
    embedding_to_bytes, save_face_image,
)
from app.face_direction import get_face_direction
from app.camera_manager import camera_manager
from app.rtsp_utils import _parse_source

router = APIRouter()

REQUIRED_ANGLES = ["THANG", "TRAI", "PHAI"]


def _event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _enroll3_generator(username: str, source: str, db: Session):
    src = _parse_source(source)

    # Khởi động camera
    if isinstance(src, int):
        if not camera_manager.is_running or camera_manager._source != src:
            if not camera_manager.start(src):
                yield _event({"error": f"Không mở được camera {src}"})
                return
        get_frame = camera_manager.get_frame
    else:
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            yield _event({"error": f"Không mở được RTSP: {source}"})
            return
        def get_frame():
            ret, f = cap.read()
            return f if ret else None

    embeddings_collected: dict[str, np.ndarray] = {}

    try:
        angle_idx = 0

        while angle_idx < len(REQUIRED_ANGLES):
            required = REQUIRED_ANGLES[angle_idx]

            frame = get_frame()
            if frame is None:
                await asyncio.sleep(0.05)
                continue

            result    = get_face_direction(frame)
            direction = result["direction"]
            annotated = result["annotated_frame"]

            h, w = annotated.shape[:2]
            cv2.putText(annotated, f"Buoc {angle_idx+1}/3: Hay quay mat {required}",
                        (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

            _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
            frame_b64 = base64.b64encode(buf.tobytes()).decode()

            # Gửi frame preview
            yield _event({
                "step": angle_idx + 1,
                "total_steps": len(REQUIRED_ANGLES),
                "required_angle": required,
                "direction": direction,
                "frame_b64": frame_b64,
                "done": False,
                "message": f"Bước {angle_idx+1}/3 — Hãy quay mặt: {required}",
            })

            # Phát hiện đúng hướng → chụp ngay
            if direction == required:
                embedding, _ = extract_embedding_from_image(frame)
                if embedding is not None:
                    embeddings_collected[required] = embedding
                    save_face_image(f"{username}_{required}", frame)

                    yield _event({
                        "step": angle_idx + 1,
                        "total_steps": len(REQUIRED_ANGLES),
                        "required_angle": required,
                        "direction": direction,
                        "frame_b64": frame_b64,
                        "done": False,
                        "captured": required,
                        "message": f"✅ Đã chụp góc {required}!",
                    })
                    angle_idx += 1
                    await asyncio.sleep(0.5)   # dừng nhỏ trước góc tiếp
                # Nếu embedding None (không detect mặt) → thử lại frame tiếp

            await asyncio.sleep(0.08)   # ~12fps

        # ── Lưu DB ──────────────────────────────────────────
        if len(embeddings_collected) == len(REQUIRED_ANGLES):
            avg = np.mean(list(embeddings_collected.values()), axis=0)
            avg /= np.linalg.norm(avg)

            user = db.query(User).filter(User.username == username).first()
            if user:
                user.face_embedding = embedding_to_bytes(avg)
            else:
                user = User(username=username, hashed_password="",
                            face_embedding=embedding_to_bytes(avg))
                db.add(user)
            db.commit()

            yield _event({
                "done": True,
                "username": username,
                "angles_captured": list(embeddings_collected.keys()),
                "message": f"✅ Đăng ký thành công 3 góc cho '{username}'!",
            })
        else:
            yield _event({
                "done": True,
                "error": f"Chỉ chụp được {len(embeddings_collected)}/3 góc. Vui lòng thử lại.",
            })

    except asyncio.CancelledError:
        pass
    except Exception as e:
        yield _event({"error": str(e)})
    finally:
        if not isinstance(src, int) and 'cap' in dir():
            cap.release()


@router.get("/enroll3", summary="Đăng ký 3 góc mặt — 1 ảnh/góc (SSE)")
async def enroll_3_angles(
    username: str = Query(...),
    source: str = Query(default="0"),
    db: Session = Depends(get_db),
):
    return StreamingResponse(
        _enroll3_generator(username, source, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
