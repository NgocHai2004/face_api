"""
app/routers/enroll_nfc.py

Đăng ký kết hợp NFC thẻ từ + khuôn mặt 3 góc (tất cả tự động).

────────────────────────────────────────────────────────────────────
FLOW TỔNG QUAN
────────────────────────────────────────────────────────────────────

1.  POST /enroll/nfc/start
        ?username=alice&position=NhanVien&expiry_date=2027-12-31
    → Khởi tạo session đăng ký. Trả về session_id.

2.  GET  /enroll/nfc/stream?username=alice              (SSE)
    → Stream real-time: hướng dẫn quay mặt 3 góc, kết quả từng góc.
      Client tự động nhận sự kiện khi camera phát hiện góc đúng.
      Đồng thời stream cũng sẽ báo khi card được quét (event nfc_scanned).

3.  POST /enroll/nfc/card
        ?username=alice&card_id=A66AB0AA
    → NFC reader module gọi khi quét được thẻ. Lưu card_id vào session.

4.  POST /enroll/nfc/finish?username=alice
    → Kết thúc đăng ký. Điều kiện thành công:
        • Đủ 3 góc mặt  (face_ok = True)   — hoặc —
        • Có card_id    (card_ok = True)    — hoặc —
        • Cả hai
      Nếu KHÔNG có gì → 422 "Chưa đủ điều kiện".
      Tính avg embedding (nếu có mặt), lưu MongoDB.

────────────────────────────────────────────────────────────────────
SESSION SCHEMA (in-memory)
────────────────────────────────────────────────────────────────────
_sessions[username] = {
    "username":    str,
    "position":    str | None,
    "expiry_date": datetime | None,
    "angles":      { "THANG": {"embedding": ndarray, "face_crop_b64": str|None}, ... },
    "card_id":     str | None,
    "finished":    bool,
}
────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
from dateutil import parser as dateutil_parser
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.database import User
from app.enroll_state import enroll_state
from app.face_direction import get_face_direction
from app.face_utils import (
    embedding_to_bytes,
    save_face_image,
    embedding_from_faces,
    extract_embedding_from_image,
)
from app.rtsp_utils import _parse_source, fetch_snapshot_from_url
from app.ws_producer import push_event_async

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enroll/nfc", tags=["Enroll NFC + Face"])

REQUIRED_ANGLES = ["THANG", "TRAI", "PHAI"]
ANGLE_LABELS    = {"THANG": "Thẳng", "TRAI": "Trái", "PHAI": "Phải"}

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


# ── In-memory session ────────────────────────────────────────────────────────
_sessions: dict[str, dict] = {}


# ── SSE helper ────────────────────────────────────────────────────────────────
def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Frame capture helper (reuse enroll3 pattern) ──────────────────────────────
def _get_frame_fn(src):
    if isinstance(src, int):
        from app.camera_manager import camera_manager
        if not camera_manager.is_running or camera_manager._source != src:
            camera_manager.start(src)
        return camera_manager.get_frame, None

    if isinstance(src, str) and src.lower().startswith("http"):
        snapshot_url = src.replace("/stream", "/snapshot")
        return lambda: fetch_snapshot_from_url(snapshot_url), None

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None, None
    return lambda: (lambda ret, f: f if ret else None)(*cap.read()), cap


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/nfc/start
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/start",
    summary="🚀 Khởi tạo session đăng ký NFC + Khuôn mặt",
    description=(
        "Bước đầu tiên: tạo session đăng ký cho user.\n\n"
        "Sau khi gọi API này:\n"
        "1. Kết nối SSE: `GET /enroll/nfc/stream?username=alice&source=0`\n"
        "2. NFC reader tự động gọi `POST /enroll/nfc/card` khi quét được thẻ\n"
        "3. Nhấn nút kết thúc: `POST /enroll/nfc/finish?username=alice`\n\n"
        "**Điều kiện kết thúc thành công:** đủ 3 góc mặt HOẶC có thẻ từ (hoặc cả hai)."
    ),
)
async def enroll_nfc_start(
    username:    str            = Query(..., description="Tên người dùng"),
    position:    str            = Query(..., description="Chức vụ (VD: Nhân viên, Quản lý, Bảo vệ)"),
    expiry_date: Optional[str]  = Query(None, description="Ngày hết hạn ISO 8601 (VD: 2027-12-31)"),
):
    if not position.strip():
        raise HTTPException(status_code=422, detail="position là bắt buộc và không được để trống")

    parsed_expiry: Optional[datetime] = None
    if expiry_date:
        try:
            parsed_expiry = dateutil_parser.parse(expiry_date)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)",
            )

    # Upsert user in DB
    user = await User.find_one(User.username == username)
    if user:
        user.position    = position.strip()
        if parsed_expiry is not None:
            user.expiry_date = parsed_expiry
        await user.save()
        user_status = "exists"
    else:
        user = User(
            username=username,
            hashed_password="",
            position=position.strip(),
            expiry_date=parsed_expiry,
        )
        await user.insert()
        user_status = "created"

    # Init / reset session
    _sessions[username] = {
        "username":    username,
        "position":    position.strip(),
        "expiry_date": parsed_expiry,
        "angles":      {},
        "card_id":     None,
        "finished":    False,
    }
    enroll_state.start(username)

    return {
        "success":     True,
        "user_status": user_status,
        "username":    username,
        "position":    position.strip(),
        "expiry_date": parsed_expiry.isoformat() if parsed_expiry else None,
        "message":     (
            f"✅ Đã tạo user '{username}'. Hãy kết nối SSE và bắt đầu đăng ký."
            if user_status == "created"
            else f"ℹ️ User '{username}' đã tồn tại. Session mới đã khởi tạo."
        ),
        "next_steps": {
            "sse_stream":   f"GET /enroll/nfc/stream?username={username}&source=0",
            "submit_card":  f"POST /enroll/nfc/card?username={username}&card_id=<UID>",
            "finish":       f"POST /enroll/nfc/finish?username={username}",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helper: _do_finish — lưu DB và push event, dùng cho cả auto_finish và /finish
# ─────────────────────────────────────────────────────────────────────────────
async def _do_finish(session: dict, username: str):
    """
    Async generator: thực hiện lưu DB từ session và yield SSE event enroll_done.
    Dùng cho cả auto_finish trong stream và endpoint /finish.
    """
    angles_collected = session["angles"]
    card_id          = session.get("card_id")
    face_ok          = len(angles_collected) == len(REQUIRED_ANGLES)
    card_ok          = bool(card_id)

    session["finished"] = True

    # Tính avg embedding nếu có đủ 3 góc
    avg_embedding = None
    if face_ok:
        embeddings = [angles_collected[a]["embedding"] for a in REQUIRED_ANGLES]
        avg = np.mean(embeddings, axis=0)
        avg = avg / np.linalg.norm(avg)
        avg_embedding = embedding_to_bytes(avg)

    position    = session.get("position")
    expiry_date = session.get("expiry_date")

    # Upsert user trong DB
    user = await User.find_one(User.username == username)
    if user:
        if avg_embedding is not None:
            user.face_embedding  = avg_embedding
            user.face_image_path = f"./face_images/{username}_THANG.jpg"
        if card_id is not None:
            user.card_id = card_id
        if position is not None:
            user.position = position
        if expiry_date is not None:
            user.expiry_date = expiry_date
        await user.save()
        saved_position = user.position
        saved_expiry   = user.expiry_date
        saved_card     = user.card_id
    else:
        user = User(
            username=username,
            hashed_password="",
            position=position,
            expiry_date=expiry_date,
            face_image_path=f"./face_images/{username}_THANG.jpg" if face_ok else None,
            card_id=card_id,
        )
        if avg_embedding is not None:
            user.face_embedding = avg_embedding
        await user.insert()
        saved_position = position
        saved_expiry   = expiry_date
        saved_card     = card_id

    # Xóa session và giải phóng trạng thái đăng ký
    _sessions.pop(username, None)
    enroll_state.finish(username)

    modes = []
    if face_ok:
        modes.append("khuôn mặt 3 góc")
    if card_ok:
        modes.append(f"thẻ NFC ({saved_card})")

    done_event = {
        "event":           "enroll_nfc_done",
        "type":            "nfc_enroll",
        "done":            True,
        "username":        username,
        "position":        saved_position or "",
        "expiry_date":     saved_expiry.isoformat() if saved_expiry else None,
        "face_ok":         face_ok,
        "card_ok":         card_ok,
        "angles_captured": list(angles_collected.keys()) if face_ok else [],
        "card_id":         saved_card,
        "registered_with": " + ".join(modes),
        "timestamp":       datetime.now().isoformat(),
        "message":         f"✅ Đăng ký thành công cho '{username}' với: {' + '.join(modes)}.",
    }
    asyncio.ensure_future(push_event_async(done_event, event_type="nfc_enroll"))

    yield _sse({**done_event})


# ─────────────────────────────────────────────────────────────────────────────
# GET /enroll/nfc/stream  (SSE – auto face capture 3 angles)
# ─────────────────────────────────────────────────────────────────────────────
async def _face_stream_generator(
    username: str,
    source: str,
    position: Optional[str] = None,
    expiry_date: Optional[datetime] = None,
    auto_finish: bool = False,
):
    """
    SSE generator: chạy vòng lặp 1fps, tự động phát hiện góc mặt.
    Dừng khi:
      - Session bị đánh dấu finished từ bên ngoài (POST /enroll/nfc/finish được gọi)
      - auto_finish=True (nội bộ): đủ 3 góc mặt → tự động lưu DB và kết thúc
    Nếu session chưa tồn tại → tự tạo session mới (không cần gọi /start trước).
    """
    # ── Tự tạo session nếu chưa có ──────────────────────────────────────────
    if username not in _sessions:
        parsed_expiry = expiry_date

        # Upsert user in DB
        user = await User.find_one(User.username == username)
        if user:
            if position is not None:
                user.position = position.strip() if position else user.position
            if parsed_expiry is not None:
                user.expiry_date = parsed_expiry
            if position is not None or parsed_expiry is not None:
                await user.save()
        else:
            user = User(
                username=username,
                hashed_password="",
                position=position.strip() if position else None,
                expiry_date=parsed_expiry,
            )
            await user.insert()

        _sessions[username] = {
            "username":    username,
            "position":    position.strip() if position else None,
            "expiry_date": parsed_expiry,
            "angles":      {},
            "card_id":     None,
            "finished":    False,
        }
        enroll_state.start(username)
        yield _sse({
            "event":    "session_created",
            "username": username,
            "message":  f"✅ Đã tạo session đăng ký cho '{username}'.",
            "done":     False,
        })

    session = _sessions.get(username)
    if session is None:
        yield _sse({"error": f"Không thể tạo session cho '{username}'."})
        return

    src = _parse_source(source)
    get_frame, cap = _get_frame_fn(src)
    if get_frame is None:
        yield _sse({"error": f"Không mở được source: {source}"})
        return

    yield _sse({
        "event":   "stream_started",
        "username": username,
        "message": f"🎬 Bắt đầu stream đăng ký cho '{username}'. Hãy quay mặt theo hướng dẫn.",
        "done":    False,
    })

    angle_idx = 0
    try:
        while not session.get("finished", False):
            # ── Kiểm tra user còn tồn tại trong DB ──────────────────────────
            user_check = await User.find_one(User.username == username)
            if user_check is None:
                yield _sse({
                    "event":   "user_deleted",
                    "username": username,
                    "done":    True,
                    "message": f"⚠️ User '{username}' đã bị xóa. Dừng đăng ký.",
                })
                session["finished"] = True
                _sessions.pop(username, None)
                break

            # Nếu đã đủ 3 góc → dừng stream mặt
            collected_angles = session["angles"]
            remaining = [a for a in REQUIRED_ANGLES if a not in collected_angles]
            if not remaining:
                yield _sse({
                    "event":           "face_complete",
                    "username":        username,
                    "angles_captured": list(collected_angles.keys()),
                    "card_id":         session.get("card_id"),
                    "done":            False,
                    "message":         "✅ Đã chụp đủ 3 góc mặt!" + (" Đang lưu..." if auto_finish else " Nhấn 'Kết thúc' để lưu."),
                })

                if auto_finish:
                    # Tự động lưu DB ngay khi đủ 3 góc (không cần gọi /finish)
                    async for event in _do_finish(session, username):
                        yield event
                    break

                # Chế độ thủ công: Tiếp tục chờ card hoặc finish signal
                while not session.get("finished", False):
                    await asyncio.sleep(0.5)
                    # Thông báo nếu card vừa được thêm
                    if session.get("card_id") and not session.get("_card_notified"):
                        session["_card_notified"] = True
                        yield _sse({
                            "event":    "nfc_scanned",
                            "username": username,
                            "card_id":  session["card_id"],
                            "done":     False,
                            "message":  f"💳 Đã quét thẻ: {session['card_id']}. Nhấn 'Kết thúc' để lưu.",
                        })
                break

            required = remaining[0]

            # Thông báo góc cần chụp (chỉ khi chuyển sang góc mới)
            if angle_idx < len(REQUIRED_ANGLES) and REQUIRED_ANGLES[angle_idx] != required:
                angle_idx = REQUIRED_ANGLES.index(required)

            yield _sse({
                "event":          "angle_instruction",
                "step":           len(collected_angles) + 1,
                "total_steps":    len(REQUIRED_ANGLES),
                "required_angle": required,
                "done":           False,
                "message":        f"Bước {len(collected_angles)+1}/3 — Hãy quay mặt: {ANGLE_LABELS[required]}",
            })

            # Vòng lặp 1fps cho góc này
            while required not in session["angles"] and not session.get("finished", False):
                await asyncio.sleep(1.0)

                # Kiểm tra card notification trong khi chờ
                if session.get("card_id") and not session.get("_card_notified"):
                    session["_card_notified"] = True
                    yield _sse({
                        "event":    "nfc_scanned",
                        "username": username,
                        "card_id":  session["card_id"],
                        "done":     False,
                        "message":  f"💳 Đã quét thẻ: {session['card_id']}",
                    })

                frame = get_frame()
                if frame is None:
                    yield _sse({
                        "event":   "frame_error",
                        "message": "⚠️ Không lấy được frame, thử lại...",
                        "done":    False,
                    })
                    continue

                fh, fw = frame.shape[:2]
                result    = get_face_direction(frame)
                direction = result["direction"]
                annotated = result["annotated_frame"]

                # Vẽ zone rectangle lên frame
                zx1 = int((fw - ZONE_W) / 2)
                zy1 = int((fh - ZONE_H) / 2)
                zx2 = zx1 + ZONE_W
                zy2 = zy1 + ZONE_H
                _faces_in_zone = [f for f in result.get("_faces", []) if _face_in_zone(f, fh, fw)]
                zone_color = (0, 255, 0) if _faces_in_zone else (0, 165, 255)
                cv2.rectangle(annotated, (zx1, zy1), (zx2, zy2), zone_color, 2)
                cv2.putText(annotated, "Dung vao day", (zx1, zy1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, zone_color, 1)

                if not _faces_in_zone:
                    _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    frame_b64 = base64.b64encode(buf.tobytes()).decode()
                    yield _sse({
                        "event":          "angle_instruction",
                        "step":           len(collected_angles) + 1,
                        "total_steps":    len(REQUIRED_ANGLES),
                        "required_angle": required,
                        "frame_b64":      frame_b64,
                        "done":           False,
                        "message":        "Hãy đứng vào giữa khung hình",
                    })
                    continue

                cv2.putText(
                    annotated,
                    f"Buoc {len(collected_angles)+1}/3: {ANGLE_LABELS.get(required, required)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2,
                )
                _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 65])
                frame_b64 = base64.b64encode(buf.tobytes()).decode()

                if direction != required:
                    yield _sse({
                        "event":          "angle_mismatch",
                        "step":           len(collected_angles) + 1,
                        "total_steps":    len(REQUIRED_ANGLES),
                        "required_angle": required,
                        "direction":      direction,
                        "frame_b64":      frame_b64,
                        "done":           False,
                        "message":        f"Cần: {ANGLE_LABELS[required]}, đang: {ANGLE_LABELS.get(direction, direction or 'chưa rõ')}",
                    })
                    continue

                # Kiểm tra user vẫn còn trong DB (tránh push khi user đã bị xóa)
                user_check = await User.find_one(User.username == username)
                if user_check is None:
                    yield _sse({
                        "event":   "user_deleted",
                        "username": username,
                        "done":    True,
                        "message": f"⚠️ User '{username}' đã bị xóa. Dừng đăng ký.",
                    })
                    session["finished"] = True
                    _sessions.pop(username, None)
                    return

                # Đúng hướng → trích embedding
                embedding, face_crop = embedding_from_faces(frame, result.get("_faces", []))
                if embedding is None:
                    yield _sse({
                        "event":   "embedding_error",
                        "message": f"⚠️ Góc {ANGLE_LABELS[required]}: không trích được embedding, thử lại...",
                        "done":    False,
                    })
                    continue

                face_crop_b64 = None
                if face_crop is not None and face_crop.size > 0:
                    _, crop_buf = cv2.imencode(".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    face_crop_b64 = base64.b64encode(crop_buf.tobytes()).decode()

                # Lưu vào session
                session["angles"][required] = {
                    "embedding":    embedding,
                    "face_crop_b64": face_crop_b64,
                }
                save_face_image(f"{username}_{required}", frame)

                angle_event = {
                    "event":          "enroll_nfc_angle",
                    "type":           "face_recognition",
                    "step":           len(session["angles"]),
                    "total_steps":    len(REQUIRED_ANGLES),
                    "required_angle": required,
                    "captured":       required,
                    "username":       username,
                    "position":       session.get("position") or "",
                    "expiry_date":    session["expiry_date"].isoformat() if session.get("expiry_date") else None,
                    "source":         source,
                    "timestamp":      datetime.now().isoformat(),
                    "face_crop_b64":  face_crop_b64,
                    "done":           False,
                    "message":        f"✅ Đã chụp góc {ANGLE_LABELS[required]} cho '{username}'!",
                }
                yield _sse({**angle_event, "frame_b64": frame_b64})
                asyncio.ensure_future(push_event_async(angle_event, event_type="nfc_enroll"))

        # Stream kết thúc
        yield _sse({
            "event":    "stream_ended",
            "username": username,
            "done":     True,
            "message":  "🔚 Stream đã kết thúc.",
        })

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(f"[enroll_nfc] Stream error for '{username}': {e}")
        yield _sse({"error": str(e), "done": True})
    finally:
        if cap is not None:
            cap.release()


@router.get(
    "/stream",
    summary="[1/2] 🎬 Bắt đầu đăng ký NFC + Khuôn mặt (SSE stream)",
    description=(
        "**Bước 1/2 — Bắt đầu phiên đăng ký.**\n\n"
        "Tự động tạo user + session rồi stream camera nhận diện 3 góc mặt.\n"
        "Song song đó NFC reader gọi `POST /enroll/nfc/card` để gửi thẻ.\n\n"
        "```\n"
        "GET /enroll/nfc/stream?username=alice&position=NhanVien&source=0\n"
        "```\n\n"
        "**Kết thúc phiên:** gọi `POST /enroll/nfc/finish?username=alice` để lưu DB.\n\n"
        "**Các SSE event:**\n"
        "- `session_created` – session vừa được tạo tự động\n"
        "- `stream_started`  – camera bắt đầu chạy\n"
        "- `angle_instruction` – hướng dẫn góc cần quay\n"
        "- `angle_mismatch`  – sai góc, chờ điều chỉnh\n"
        "- `enroll_nfc_angle` – ✅ chụp 1 góc thành công\n"
        "- `nfc_scanned`     – 💳 thẻ NFC vừa quét\n"
        "- `face_complete`   – đủ 3 góc, chờ lệnh finish\n"
        "- `stream_ended`    – stream kết thúc\n\n"
        "**NFC reader song song:**\n"
        "```\n"
        "POST /enroll/nfc/card?username=alice&card_id=A66AB0AA\n"
        "```"
    ),
)
async def enroll_nfc_stream(
    username:    str           = Query(...,         description="Tên người dùng"),
    source:      str           = Query(default="0", description="Camera source: 0 = webcam, hoặc RTSP/HTTP URL"),
    position:    Optional[str] = Query(None,        description="Chức vụ (VD: NhanVien, QuanLy)"),
    expiry_date: Optional[str] = Query(None,        description="Ngày hết hạn ISO 8601 (VD: 2027-12-31)"),
):
    parsed_expiry: Optional[datetime] = None
    if expiry_date:
        try:
            parsed_expiry = dateutil_parser.parse(expiry_date)
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)"},
            )

    return StreamingResponse(
        _face_stream_generator(username, source, position=position, expiry_date=parsed_expiry, auto_finish=False),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/nfc/card  — NFC reader module gọi khi quét được thẻ
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/card",
    summary="💳 Nhận card_id từ NFC reader",
    description=(
        "NFC reader module (module-rfid-nfc-13-56mhz-pn532) gọi endpoint này khi quét được thẻ.\n\n"
        "card_id sẽ được lưu vào session đăng ký của username.\n\n"
        "Nếu chưa có session → tạo session pending (chờ start hoặc dùng trực tiếp với finish)."
    ),
)
async def enroll_nfc_card(
    username: str = Query(..., description="Tên người dùng đang đăng ký"),
    card_id:  str = Query(..., description="UID thẻ NFC dạng hex in hoa (VD: A66AB0AA)"),
):
    card_id_upper = card_id.upper().strip()
    if not card_id_upper:
        raise HTTPException(status_code=422, detail="card_id không được để trống")

    # ── Kiểm tra card_id đã thuộc về user khác chưa ──────────────────────────
    existing_owner = await User.find_one(User.card_id == card_id_upper)
    if existing_owner is not None and existing_owner.username != username:
        ts = datetime.now().isoformat()
        event_data = {
            "event":          "enroll_card_duplicate",
            "type":           "card_reader",
            "card_id":        card_id_upper,
            "requested_by":   username,
            "current_owner":  existing_owner.username,
            "current_owner_position": existing_owner.position or "",
            "current_owner_expiry":   existing_owner.expiry_date.isoformat() if existing_owner.expiry_date else None,
            "matched":        False,
            "reason":         "card_already_registered",
            "timestamp":      ts,
            "message":        f"❌ Thẻ {card_id_upper} đã được đăng ký cho '{existing_owner.username}' ({existing_owner.position or 'N/A'})",
        }
        asyncio.ensure_future(push_event_async(event_data, event_type="card_reader"))
        logger.warning(f"[enroll_nfc] Card {card_id_upper} already owned by '{existing_owner.username}', rejected for '{username}'")
        return JSONResponse(
            status_code=409,
            content={
                "success":       False,
                "card_id":       card_id_upper,
                "username":      username,
                "current_owner": existing_owner.username,
                "reason":        "card_already_registered",
                "timestamp":     ts,
                "message":       f"❌ Thẻ {card_id_upper} đã được đăng ký cho user '{existing_owner.username}'. Không thể dùng lại.",
            },
        )

    # Tạo session tạm nếu chưa có (trường hợp quét thẻ trước khi start)
    if username not in _sessions:
        _sessions[username] = {
            "username":    username,
            "position":    None,
            "expiry_date": None,
            "angles":      {},
            "card_id":     None,
            "finished":    False,
        }

    session = _sessions[username]
    if session.get("finished"):
        raise HTTPException(status_code=409, detail=f"Session của '{username}' đã kết thúc. Gọi /start để bắt đầu lại.")

    old_card = session.get("card_id")
    session["card_id"] = card_id_upper
    # Reset notification flag để SSE stream thông báo lại
    session.pop("_card_notified", None)

    logger.info(f"[enroll_nfc] Card registered for '{username}': {card_id_upper}")

    # Push event thẻ đã được quét thành công
    ts_card = datetime.now().isoformat()
    card_event = {
        "event":     "enroll_nfc_card",
        "type":      "card_reader",
        "card_id":   card_id_upper,
        "username":  username,
        "position":  session.get("position") or "",
        "replaced":  old_card is not None and old_card != card_id_upper,
        "old_card":  old_card,
        "timestamp": ts_card,
        "message":   f"💳 Thẻ {card_id_upper} đã được quét cho '{username}'.",
    }
    asyncio.ensure_future(push_event_async(card_event, event_type="card_reader"))

    return {
        "success":  True,
        "username": username,
        "card_id":  card_id_upper,
        "replaced": old_card is not None and old_card != card_id_upper,
        "old_card": old_card,
        "message":  f"✅ Đã lưu card_id '{card_id_upper}' cho user '{username}'.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/nfc/finish  — Kết thúc đăng ký
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/finish",
    summary="[2/2] 🏁 Kết thúc đăng ký — lưu vào database",
    description=(
        "**Bước 2/2 — Kết thúc phiên đăng ký và lưu vào MongoDB.**\n\n"
        "Gọi sau `GET /enroll/nfc/stream` khi đã đủ dữ liệu.\n\n"
        "```\n"
        "POST /enroll/nfc/finish?username=alice\n"
        "```\n\n"
        "**Điều kiện thành công (ít nhất 1 trong 2):**\n"
        "- ✅ Đã chụp đủ 3 góc khuôn mặt\n"
        "- ✅ Đã quét được thẻ NFC (card_id)\n\n"
        "**Nếu cả hai đều không đủ** → HTTP 422 với thông tin còn thiếu.\n\n"
        "**Nếu có cả face + card** → lưu cả hai vào user record.\n\n"
        "Response thành công:\n"
        "```json\n"
        "{ \"success\": true, \"face_ok\": true, \"card_ok\": true,\n"
        "  \"card_id\": \"A66AB0AA\", \"angles_captured\": [\"THANG\",\"TRAI\",\"PHAI\"],\n"
        "  \"registered_with\": \"khuôn mặt 3 góc + thẻ NFC (A66AB0AA)\" }\n"
        "```\n\n"
        "Sau khi gọi endpoint này, SSE stream sẽ tự động kết thúc."
    ),
)
async def enroll_nfc_finish(
    username: str = Query(..., description="Tên người dùng"),
):
    session = _sessions.get(username)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy session cho '{username}'. Gọi /enroll/nfc/start hoặc /enroll/nfc/stream trước.",
        )
    if session.get("finished"):
        raise HTTPException(status_code=409, detail=f"Session của '{username}' đã kết thúc trước đó.")

    angles_collected = session["angles"]
    card_id          = session.get("card_id")
    face_ok          = len(angles_collected) == len(REQUIRED_ANGLES)
    card_ok          = bool(card_id)

    # ── Kiểm tra điều kiện ───────────────────────────────────────────────────
    if not face_ok and not card_ok:
        missing_angles = [a for a in REQUIRED_ANGLES if a not in angles_collected]
        return JSONResponse(
            status_code=422,
            content={
                "success":         False,
                "satisfied":       False,
                "face_ok":         face_ok,
                "card_ok":         card_ok,
                "angles_captured": list(angles_collected.keys()),
                "missing_angles":  missing_angles,
                "card_id":         None,
                "username":        username,
                "message": (
                    f"❌ Chưa đủ điều kiện đăng ký cho '{username}'.\n"
                    f"  • Khuôn mặt: {len(angles_collected)}/3 góc "
                    f"(còn thiếu: {', '.join(ANGLE_LABELS[a] for a in missing_angles)})\n"
                    f"  • Thẻ NFC: chưa quét\n"
                    f"Cần ít nhất 1 trong 2: đủ 3 góc mặt HOẶC quét thẻ NFC."
                ),
            },
        )

    # ── Đủ điều kiện → dùng _do_finish để lưu DB ─────────────────────────────
    result_event: dict = {}
    async for sse_chunk in _do_finish(session, username):
        # _do_finish yield _sse(...) string, parse lại để lấy dict
        import json as _json
        raw = sse_chunk.replace("data: ", "").strip()
        if raw:
            result_event = _json.loads(raw)

    return {
        "success":         True,
        "satisfied":       True,
        "username":        result_event.get("username", username),
        "position":        result_event.get("position"),
        "expiry_date":     result_event.get("expiry_date"),
        "face_ok":         result_event.get("face_ok", face_ok),
        "card_ok":         result_event.get("card_ok", card_ok),
        "angles_captured": result_event.get("angles_captured", []),
        "card_id":         result_event.get("card_id"),
        "registered_with": result_event.get("registered_with", ""),
        "message":         result_event.get("message", f"✅ Đã đăng ký '{username}'."),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /enroll/nfc/session-status  — Xem trạng thái session
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/session-status",
    summary="📋 Xem trạng thái session đăng ký NFC + Face",
)
def enroll_nfc_status(username: str = Query(..., description="Tên người dùng")):
    session = _sessions.get(username)
    if session is None:
        return {
            "has_session": False,
            "username":    username,
            "message":     f"Không có session cho '{username}'. Gọi /enroll/nfc/start để bắt đầu.",
        }
    angles_collected = session["angles"]
    missing          = [a for a in REQUIRED_ANGLES if a not in angles_collected]
    return {
        "has_session":     True,
        "username":        username,
        "position":        session.get("position"),
        "expiry_date":     session["expiry_date"].isoformat() if session["expiry_date"] else None,
        "angles_captured": list(angles_collected.keys()),
        "missing_angles":  missing,
        "face_ok":         len(missing) == 0,
        "card_id":         session.get("card_id"),
        "card_ok":         bool(session.get("card_id")),
        "finished":        session.get("finished", False),
        "ready_to_finish": len(missing) == 0 or bool(session.get("card_id")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /enroll/nfc/reset  — Xóa session
# ─────────────────────────────────────────────────────────────────────────────
@router.delete(
    "/reset",
    summary="🔄 Xóa session đăng ký NFC + Face",
)
def enroll_nfc_reset(username: str = Query(..., description="Tên người dùng")):
    if username in _sessions:
        del _sessions[username]
        enroll_state.finish(username)
        return {"success": True, "message": f"Đã reset session cho '{username}'"}
    return {"success": False, "message": f"Không có session nào cho '{username}'"}
