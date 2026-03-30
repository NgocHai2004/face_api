"""
app/routers/verify3.py

SSE endpoint xác thực khuôn mặt 3 góc:
  GET /verify3?source=0[&username=alice]

Flow: TRAI → THANG → PHAI
  Mỗi 1 giây: lấy 1 snapshot → detect hướng → nếu đúng chạy InsightFace → chuyển góc
  Kết quả tổng hợp: cần ≥ 2/3 góc khớp
"""
from fastapi import APIRouter, Query, Depends
from fastapi.responses import StreamingResponse
import cv2
import asyncio
import base64
import time
import numpy as np
import json
from typing import Optional
from sqlalchemy.orm import Session

from app.database import get_db, User
from app.face_utils import (
    extract_embedding_from_image,
    embedding_from_faces,
    bytes_to_embedding, verify_faces,
)
from app.face_direction import get_face_direction
from app.rtsp_utils import _parse_source, fetch_snapshot_from_url
from app.config import settings
from app.ws_producer import push_event_async
from datetime import datetime

router = APIRouter()

VERIFY_ANGLES = ["TRAI", "THANG", "PHAI"]


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


async def _verify3_generator(source: str, username: Optional[str], db: Session):
    src = _parse_source(source)

    # Load users
    query = db.query(User).filter(User.face_embedding.isnot(None))
    if username:
        query = query.filter(User.username == username)
    users = query.all()

    if not users:
        yield _event({"error": "Không có khuôn mặt nào trong DB. Hãy đăng ký trước."})
        return

    get_frame, cap = _get_frame_fn(src)
    if get_frame is None:
        yield _event({"error": f"Không mở được source: {source}"})
        return

    angle_results: dict[str, dict] = {}

    try:
        for angle_idx, required in enumerate(VERIFY_ANGLES):
            # Thông báo bắt đầu góc mới
            yield _event({
                "step": angle_idx + 1,
                "total_steps": len(VERIFY_ANGLES),
                "required_angle": required,
                "done": False,
                "message": f"Bước {angle_idx+1}/3 — Hãy quay mặt: {required}",
            })

            # Vòng lặp 1fps cho góc này
            while True:
                await asyncio.sleep(1.0)  # 1s/lần

                frame = get_frame()
                if frame is None:
                    yield _event({"step": angle_idx+1, "done": False,
                                  "message": "⚠️ Không lấy được frame, thử lại..."})
                    continue

                # Detect hướng (YuNet ~10-20ms)
                result = get_face_direction(frame)
                direction = result["direction"]
                annotated = result["annotated_frame"]

                # Encode preview frame
                cv2.putText(annotated, f"Buoc {angle_idx+1}/3: Quay mat {required}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
                _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 65])
                frame_b64 = base64.b64encode(buf.tobytes()).decode()

                if direction != required:
                    # Sai hướng → gửi preview, chờ 1s tiếp
                    yield _event({
                        "step": angle_idx + 1,
                        "total_steps": len(VERIFY_ANGLES),
                        "required_angle": required,
                        "direction": direction,
                        "frame_b64": frame_b64,
                        "done": False,
                        "message": f"Bước {angle_idx+1}/3 — Cần: {required}, đang: {direction or 'chưa rõ'}",
                    })
                    continue

                # Đúng hướng → tái sử dụng kết quả InsightFace từ get_face_direction (tránh gọi 2 lần)
                embedding, face_crop = embedding_from_faces(frame, result.get("_faces", []))
                best_score = 0.0
                best_user = None
                matched = False

                if embedding is not None:
                    for u in users:
                        stored = bytes_to_embedding(u.face_embedding)
                        _, score = verify_faces(stored, embedding)
                        if score > best_score:
                            best_score = score
                            best_user = u
                    matched = best_score >= settings.FACE_THRESHOLD

                # Encode crop
                face_crop_b64 = None
                if face_crop is not None and face_crop.size > 0:
                    _, crop_buf = cv2.imencode(".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    face_crop_b64 = base64.b64encode(crop_buf.tobytes()).decode()

                ANGLE_PASS_THRESHOLD = 0.6
                if best_score < ANGLE_PASS_THRESHOLD:
                    yield _event({
                        "step": angle_idx + 1,
                        "total_steps": len(VERIFY_ANGLES),
                        "required_angle": required,
                        "direction": direction,
                        "frame_b64": frame_b64,
                        "face_crop_b64": face_crop_b64,
                        "done": False,
                        "score": round(best_score, 4),
                        "retry": True,
                        "message": f"⚠️ Góc {required}: score {best_score:.3f} < 0.6 — Thử lại",
                    })
                    continue

                # Góc hợp lệ → lưu kết quả
                angle_results[required] = {
                    "matched": matched,
                    "username": best_user.username if best_user else None,
                    "score": round(best_score, 4),
                }

                angle_event = {
                    "event": "verify3_angle",
                    "step": angle_idx + 1,
                    "total_steps": len(VERIFY_ANGLES),
                    "required_angle": required,
                    "face_direction": direction,
                    "captured": required,
                    "matched": matched,
                    "username": best_user.username if best_user else None,
                    "score": round(best_score, 4),
                    "source": source,
                    "timestamp": datetime.now().isoformat(),
                    "face_crop_b64": face_crop_b64,
                    "message": (f"{'✅' if matched else '❌'} Góc {required}: "
                                f"{best_user.username if matched else 'Không khớp'} ({best_score:.3f})"),
                }
                yield _event({**angle_event, "frame_b64": frame_b64, "done": False,
                              "angle_result": angle_results[required]})
                asyncio.ensure_future(push_event_async(angle_event))
                break  # chuyển sang góc tiếp theo

        # ── Tổng hợp kết quả ────────────────────────────────────
        votes: dict[str, int] = {}
        for r in angle_results.values():
            if r["matched"] and r["username"]:
                votes[r["username"]] = votes.get(r["username"], 0) + 1

        final_match = False
        final_user = None
        final_score = 0.0

        if votes:
            final_user = max(votes, key=lambda k: votes[k])
            final_match = votes[final_user] >= 2
            scores = [r["score"] for r in angle_results.values()
                      if r["matched"] and r["username"] == final_user]
            final_score = round(sum(scores) / len(scores), 4) if scores else 0.0

        yield _event({
            "done": True,
            "matched": final_match,
            "username": final_user if final_match else None,
            "final_score": final_score,
            "angle_results": angle_results,
            "votes": votes,
            "message": (f"✅ Xác thực thành công: {final_user} ({final_score})"
                        if final_match else
                        "❌ Xác thực thất bại — không khớp đủ 2/3 góc"),
        })

    except asyncio.CancelledError:
        pass
    except Exception as e:
        yield _event({"error": str(e)})
    finally:
        if cap is not None:
            cap.release()


@router.get("/verify3", summary="Xác thực 3 góc — 1fps/góc (SSE, TRAI→THANG→PHAI)")
async def verify_3_angles(
    source: str = Query(default="0"),
    username: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    return StreamingResponse(
        _verify3_generator(source, username, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
