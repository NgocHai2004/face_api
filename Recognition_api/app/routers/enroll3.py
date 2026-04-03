"""
app/routers/enroll3.py

SSE endpoint đăng ký khuôn mặt 3 góc:
  GET /enroll3?username=alice&source=0

Flow: THANG → TRAI → PHAI
  Mỗi 1 giây: lấy 1 snapshot → detect hướng → nếu đúng hướng → trích embedding → chuyển góc
  Cuối: lưu embedding trung bình vào MongoDB
"""
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from typing import Optional
import cv2
import asyncio
import base64
import numpy as np
import json
from datetime import datetime
from dateutil import parser as dateutil_parser

from app.database import User
from app.face_utils import (
    embedding_from_faces,
    embedding_to_bytes,
    save_face_image,
)
from app.face_direction import get_face_direction
from app.rtsp_utils import _parse_source, fetch_snapshot_from_url
from app.ws_producer import push_event_async

router = APIRouter()

REQUIRED_ANGLES = ["THANG", "TRAI", "PHAI"]

# Zone nhận diện: chỉ xử lý mặt có tâm nằm trong vùng 200×300 ở giữa frame
ZONE_W = 200
ZONE_H = 300


def _face_in_zone(face, frame_h: int, frame_w: int) -> bool:
    x1, y1, x2, y2 = face.bbox[:4]
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    zone_x1 = (frame_w - ZONE_W) / 2
    zone_y1 = (frame_h - ZONE_H) / 2
    return zone_x1 <= cx <= zone_x1 + ZONE_W and zone_y1 <= cy <= zone_y1 + ZONE_H


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


async def _enroll3_generator(username: str, source: str, position: Optional[str] = None, expiry_date: Optional[datetime] = None):
    # ── Kiểm tra / tạo user ─────────────────────────────────
    user = await User.find_one(User.username == username)
    if user:
        if position is not None:
            user.position = position
        if expiry_date is not None:
            user.expiry_date = expiry_date
        if position is not None or expiry_date is not None:
            await user.save()
        yield _event({
            "user_status": "exists",
            "username": username,
            "position": user.position,
            "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
            "message": f"ℹ️ Đăng ký khuôn mặt cho '{username}'.",
            "done": False,
        })
    else:
        user = User(username=username, hashed_password="", position=position, expiry_date=expiry_date)
        await user.insert()
        yield _event({
            "user_status": "created",
            "username": username,
            "position": position,
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
            "message": f"✅ Đã tạo user '{username}' — tiến hành đăng ký khuôn mặt.",
            "done": False,
        })

    src = _parse_source(source)

    get_frame, cap = _get_frame_fn(src)
    if get_frame is None:
        yield _event({"error": f"Không mở được source: {source}"})
        return

    embeddings_collected: dict[str, np.ndarray] = {}

    try:
        for angle_idx, required in enumerate(REQUIRED_ANGLES):
            # Thông báo bắt đầu góc mới
            yield _event({
                "step": angle_idx + 1,
                "total_steps": len(REQUIRED_ANGLES),
                "required_angle": required,
                "done": False,
                "message": f"Bước {angle_idx+1}/3 — Hãy quay mặt: {required}",
            })

            # Vòng lặp 1fps cho góc này
            while True:
                await asyncio.sleep(1.0)

                # ── Kiểm tra user còn tồn tại trong DB ──────────────────
                _user_check = await User.find_one(User.username == username)
                if _user_check is None:
                    yield _event({
                        "event":   "user_deleted",
                        "username": username,
                        "done":    True,
                        "message": f"⚠️ User '{username}' đã bị xóa. Dừng đăng ký.",
                    })
                    return

                frame = get_frame()
                if frame is None:
                    yield _event({"step": angle_idx+1, "done": False,
                                  "message": "⚠️ Không lấy được frame, thử lại..."})
                    continue

                fh, fw = frame.shape[:2]

                # Detect hướng
                result = get_face_direction(frame)
                direction = result["direction"]
                annotated = result["annotated_frame"]

                # Vẽ zone rectangle lên frame
                zx1 = int((fw - ZONE_W) / 2)
                zy1 = int((fh - ZONE_H) / 2)
                zx2 = zx1 + ZONE_W
                zy2 = zy1 + ZONE_H
                # Kiểm tra mặt nằm trong zone
                _faces_in_zone = [f for f in result.get("_faces", []) if _face_in_zone(f, fh, fw)]
                zone_color = (0, 255, 0) if _faces_in_zone else (0, 165, 255)
                cv2.rectangle(annotated, (zx1, zy1), (zx2, zy2), zone_color, 2)
                cv2.putText(annotated, "Dung vao day", (zx1, zy1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, zone_color, 1)

                if not _faces_in_zone:
                    _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    frame_b64 = base64.b64encode(buf.tobytes()).decode()
                    yield _event({
                        "step": angle_idx + 1,
                        "total_steps": len(REQUIRED_ANGLES),
                        "required_angle": required,
                        "frame_b64": frame_b64,
                        "done": False,
                        "message": f"Bước {angle_idx+1}/3 — Hãy đứng vào giữa khung hình",
                    })
                    continue

                # Encode preview frame
                cv2.putText(annotated, f"Buoc {angle_idx+1}/3: Quay mat {required}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
                _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 65])
                frame_b64 = base64.b64encode(buf.tobytes()).decode()

                if direction != required:
                    yield _event({
                        "step": angle_idx + 1,
                        "total_steps": len(REQUIRED_ANGLES),
                        "required_angle": required,
                        "direction": direction,
                        "frame_b64": frame_b64,
                        "done": False,
                        "message": f"Bước {angle_idx+1}/3 — Cần: {required}, đang: {direction or 'chưa rõ'}",
                    })
                    continue

                # Kiểm tra user vẫn còn trong DB (tránh push khi user đã bị xóa)
                user_check = await User.find_one(User.username == username)
                if user_check is None:
                    yield _event({
                        "event":   "user_deleted",
                        "username": username,
                        "done":    True,
                        "message": f"⚠️ User '{username}' đã bị xóa. Dừng đăng ký.",
                    })
                    return

                # Đúng hướng → trích embedding
                embedding, face_crop = embedding_from_faces(frame, result.get("_faces", []))

                if embedding is None:
                    yield _event({
                        "step": angle_idx + 1,
                        "total_steps": len(REQUIRED_ANGLES),
                        "required_angle": required,
                        "direction": direction,
                        "frame_b64": frame_b64,
                        "done": False,
                        "message": f"⚠️ Góc {required}: không trích được embedding, thử lại...",
                    })
                    continue

                # Encode crop
                face_crop_b64 = None
                if face_crop is not None and face_crop.size > 0:
                    _, crop_buf = cv2.imencode(".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    face_crop_b64 = base64.b64encode(crop_buf.tobytes()).decode()

                # Lưu embedding + ảnh
                embeddings_collected[required] = embedding
                save_face_image(f"{username}_{required}", frame)

                angle_event = {
                    "event": "enroll3_angle",
                    "type":  "face_recognition",
                    "step": angle_idx + 1,
                    "total_steps": len(REQUIRED_ANGLES),
                    "required_angle": required,
                    "face_direction": direction,
                    "captured": required,
                    "username": username,
                    "position": position or "",
                    "expiry_date": expiry_date.isoformat() if expiry_date else None,
                    "source": source,
                    "timestamp": datetime.now().isoformat(),
                    "face_crop_b64": face_crop_b64,
                    "message": f"✅ Đã chụp góc {required} cho '{username}'!",
                }
                yield _event({**angle_event, "frame_b64": frame_b64, "done": False})
                asyncio.ensure_future(push_event_async(angle_event))
                break  # chuyển sang góc tiếp theo

        # ── Lưu MongoDB ──────────────────────────────────────
        if len(embeddings_collected) == len(REQUIRED_ANGLES):
            avg = np.mean(list(embeddings_collected.values()), axis=0)
            avg /= np.linalg.norm(avg)

            user = await User.find_one(User.username == username)
            if user:
                user.face_embedding = embedding_to_bytes(avg)
                await user.save()
            else:
                new_user = User(username=username, hashed_password="")
                new_user.face_embedding = embedding_to_bytes(avg)
                await new_user.insert()

            done_event = {
                "event": "enroll3_done",
                "type":  "face_recognition",
                "done": True,
                "username": username,
                "position": position or "",
                "expiry_date": expiry_date.isoformat() if expiry_date else None,
                "angles_captured": list(embeddings_collected.keys()),
                "source": source,
                "timestamp": datetime.now().isoformat(),
                "message": f"✅ Đăng ký thành công 3 góc cho '{username}'!",
            }
            yield _event(done_event)
            asyncio.ensure_future(push_event_async(done_event))
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
        if cap is not None:
            cap.release()


@router.get("/enroll3", summary="Đăng ký 3 góc mặt — 1fps/góc (SSE, THANG→TRAI→PHAI)")
async def enroll_3_angles(
    username: str = Query(...),
    source: str = Query(default="0"),
    position: str = Query(..., description="Chức vụ bắt buộc (VD: Nhân viên, Quản lý, Bảo vệ)"),
    expiry_date: Optional[str] = Query(None, description="Ngày hết hạn (ISO 8601, VD: 2027-12-31)"),
):
    from fastapi.responses import JSONResponse
    if not position.strip():
        return JSONResponse(
            status_code=422,
            content={"detail": "position là bắt buộc và không được để trống"},
        )
    parsed_expiry: Optional[datetime] = None
    if expiry_date:
        try:
            parsed_expiry = dateutil_parser.parse(expiry_date)
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"detail": f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)"},
            )
    return StreamingResponse(
        _enroll3_generator(username, source, position.strip(), parsed_expiry),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
