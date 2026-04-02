"""
app/routers/enroll_manual.py

Đăng ký khuôn mặt thủ công — người dùng chụp riêng từng góc:

  POST /enroll/init-user      ?username=alice
    → Kiểm tra & tạo user (1 API duy nhất)

  POST /enroll/capture/thang  ?username=alice&source=0
  POST /enroll/capture/trai   ?username=alice&source=0
  POST /enroll/capture/phai   ?username=alice&source=0
    → Chụp 1 frame, kiểm tra hướng mặt, lưu tạm session

  POST /enroll/save           ?username=alice
    → Xác nhận đủ 3 góc → tính embedding trung bình → lưu MongoDB

  GET  /enroll/status         ?username=alice
    → Xem trạng thái session

  DELETE /enroll/reset        ?username=alice
    → Xóa session
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime
import cv2
import numpy as np
import base64
from dateutil import parser as dateutil_parser

from app.database import User
from app.face_utils import (
    extract_embedding_from_image,
    embedding_to_bytes,
    save_face_image,
)
from app.face_direction import get_face_direction
from app.rtsp_utils import capture_frame_from_rtsp

router = APIRouter()

REQUIRED_ANGLES = ["THANG", "TRAI", "PHAI"]
ANGLE_LABELS    = {"THANG": "Thẳng", "TRAI": "Trái", "PHAI": "Phải"}

# ── In-memory session: {username: {angle: {embedding, face_crop_b64}}} ──
_sessions: dict[str, dict] = {}


# ── Helpers ──────────────────────────────────────────────────────────────────
def _capture_frame(source: str) -> Optional[np.ndarray]:
    return capture_frame_from_rtsp(source)


def _img_b64(img: np.ndarray, q: int = 85) -> str:
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return base64.b64encode(buf.tobytes()).decode()


def _session_status(username: str) -> dict:
    s = _sessions.get(username, {})
    return {a: (a in s) for a in REQUIRED_ANGLES}


def _capture_for_angle(username: str, angle: str, source: str) -> dict:
    """Core logic: chụp frame, kiểm tra góc, lưu session nếu hợp lệ."""
    frame = _capture_frame(source)
    if frame is None:
        raise HTTPException(status_code=503, detail=f"Không lấy được frame từ '{source}'")

    embedding, face_crop = extract_embedding_from_image(frame)
    frame_b64 = _img_b64(frame, 70)

    if embedding is None:
        return {
            "success": False,
            "username": username,
            "requested_angle": angle,
            "detected_angle": None,
            "angle_match": False,
            "frame_b64": frame_b64,
            "face_crop_b64": None,
            "session": _session_status(username),
            "missing_angles": [a for a in REQUIRED_ANGLES if a not in _sessions.get(username, {})],
            "message": "❌ Không phát hiện khuôn mặt. Hãy nhìn thẳng vào camera và thử lại.",
        }

    direction_result = get_face_direction(frame)
    detected = direction_result["direction"]
    face_b64 = _img_b64(face_crop, 85) if face_crop is not None else None
    match    = detected == angle

    if match:
        if username not in _sessions:
            _sessions[username] = {}
        _sessions[username][angle] = {"embedding": embedding, "face_crop_b64": face_b64}
        save_face_image(f"{username}_{angle}", frame)

    status  = _session_status(username)
    missing = [a for a in REQUIRED_ANGLES if not status[a]]

    return {
        "success": match,
        "username": username,
        "requested_angle": angle,
        "detected_angle": detected,
        "angle_match": match,
        "frame_b64": frame_b64,
        "face_crop_b64": face_b64,
        "session": status,
        "missing_angles": missing,
        "message": (
            f"✅ Góc {ANGLE_LABELS[angle]} hợp lệ! "
            + (f"Còn thiếu: {', '.join(ANGLE_LABELS[a] for a in missing)}" if missing else "Đủ 3 góc!")
        ) if match else (
            f"⚠️ Phát hiện hướng '{ANGLE_LABELS.get(detected, detected)}' nhưng yêu cầu '{ANGLE_LABELS[angle]}'. "
            "Hãy điều chỉnh và chụp lại."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/capture/thang
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/capture/thang",
    summary="📸 Chụp góc THẲNG",
    description=(
        "Chụp 1 frame từ camera, kiểm tra xem mặt có quay **thẳng** không.\n\n"
        "- Nếu đúng góc → lưu tạm vào session, trả về `success: true`\n"
        "- Nếu sai góc → trả về `success: false` + góc phát hiện được\n\n"
        "Gọi lại nhiều lần nếu cần đến khi `success: true`."
    ),
)
def capture_thang(
    username: str = Query(..., description="Tên người dùng cần đăng ký"),
    source:   str = Query(default="0", description="Camera source: 0 = webcam/Pi cam, hoặc RTSP URL"),
):
    return _capture_for_angle(username, "THANG", source)


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/capture/trai
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/capture/trai",
    summary="📸 Chụp góc TRÁI",
    description=(
        "Chụp 1 frame từ camera, kiểm tra xem mặt có quay **sang trái** không.\n\n"
        "- Nếu đúng góc → lưu tạm vào session, trả về `success: true`\n"
        "- Nếu sai góc → trả về `success: false` + góc phát hiện được\n\n"
        "Gọi lại nhiều lần nếu cần đến khi `success: true`."
    ),
)
def capture_trai(
    username: str = Query(..., description="Tên người dùng cần đăng ký"),
    source:   str = Query(default="0", description="Camera source: 0 = webcam/Pi cam, hoặc RTSP URL"),
):
    return _capture_for_angle(username, "TRAI", source)


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/capture/phai
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/capture/phai",
    summary="📸 Chụp góc PHẢI",
    description=(
        "Chụp 1 frame từ camera, kiểm tra xem mặt có quay **sang phải** không.\n\n"
        "- Nếu đúng góc → lưu tạm vào session, trả về `success: true`\n"
        "- Nếu sai góc → trả về `success: false` + góc phát hiện được\n\n"
        "Gọi lại nhiều lần nếu cần đến khi `success: true`."
    ),
)
def capture_phai(
    username: str = Query(..., description="Tên người dùng cần đăng ký"),
    source:   str = Query(default="0", description="Camera source: 0 = webcam/Pi cam, hoặc RTSP URL"),
):
    return _capture_for_angle(username, "PHAI", source)


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/save
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/save",
    summary="💾 Lưu 3 góc vào database",
    description=(
        "Xác nhận đăng ký sau khi đã chụp đủ 3 góc (THẲNG + TRÁI + PHẢI).\n\n"
        "Hệ thống tính **embedding trung bình** từ 3 góc rồi lưu vào MongoDB.\n\n"
        "Nếu còn thiếu góc nào → trả về lỗi 400 kèm danh sách góc còn thiếu.\n\n"
        "Sau khi lưu thành công, session tạm sẽ bị xóa."
    ),
)
async def enroll_save(
    username: str = Query(..., description="Tên người dùng"),
    position: Optional[str] = Query(None, description="Chức vụ (để trống nếu đã đặt ở init-user)"),
    expiry_date: Optional[str] = Query(None, description="Ngày hết hạn (ISO 8601, VD: 2027-12-31). Để trống nếu không thay đổi."),
):
    session = _sessions.get(username, {})
    missing = [a for a in REQUIRED_ANGLES if a not in session]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Chưa đủ 3 góc. Còn thiếu: {', '.join(ANGLE_LABELS[a] for a in missing)}",
        )

    parsed_expiry: Optional[datetime] = None
    if expiry_date:
        try:
            parsed_expiry = dateutil_parser.parse(expiry_date)
        except Exception:
            raise HTTPException(status_code=400, detail=f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)")

    embeddings = [session[a]["embedding"] for a in REQUIRED_ANGLES]
    avg = np.mean(embeddings, axis=0)
    avg = avg / np.linalg.norm(avg)

    saved_position: Optional[str] = None
    saved_expiry: Optional[datetime] = None
    user = await User.find_one(User.username == username)
    if user:
        user.face_embedding  = embedding_to_bytes(avg)
        user.face_image_path = f"./face_images/{username}_THANG.jpg"
        if position is not None:
            user.position = position
        if parsed_expiry is not None:
            user.expiry_date = parsed_expiry
        await user.save()
        saved_position = user.position
        saved_expiry = user.expiry_date
    else:
        new_user = User(
            username=username,
            hashed_password="",
            position=position,
            expiry_date=parsed_expiry,
            face_image_path=f"./face_images/{username}_THANG.jpg",
        )
        new_user.face_embedding = embedding_to_bytes(avg)
        await new_user.insert()
        saved_position = position
        saved_expiry = parsed_expiry

    del _sessions[username]

    return {
        "success": True,
        "username": username,
        "position": saved_position,
        "expiry_date": saved_expiry.isoformat() if saved_expiry else None,
        "message": f"✅ Đã lưu khuôn mặt 3 góc cho '{username}' thành công!",
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /enroll/status
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/status",
    summary="📋 Xem trạng thái session đăng ký",
    description="Trả về danh sách các góc đã chụp và các góc còn thiếu cho username.",
)
def enroll_status(username: str = Query(..., description="Tên người dùng")):
    status  = _session_status(username)
    missing = [a for a in REQUIRED_ANGLES if not status[a]]
    return {
        "username": username,
        "collected": status,
        "missing_angles": missing,
        "ready_to_save": len(missing) == 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /enroll/init-user
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/init-user",
    summary="🔎➕ Kiểm tra & tạo user (gộp 1 API)",
    description=(
        "Gọi **1 lần duy nhất** trước khi chụp 3 góc.\n\n"
        "- Nếu username **chưa có** trong DB → tự động tạo mới và trả về `created: true`\n"
        "- Nếu username **đã có**             → trả về thông tin hiện tại, `created: false`\n\n"
        "**Không bao giờ báo lỗi** nếu username hợp lệ.\n\n"
        "Sau khi gọi API này, tiếp tục:\n"
        "1. `POST /enroll/capture/thang`\n"
        "2. `POST /enroll/capture/trai`\n"
        "3. `POST /enroll/capture/phai`\n"
        "4. `POST /enroll/save`"
    ),
)
async def init_user(
    username: str = Query(..., description="Tên người dùng"),
    position: Optional[str] = Query(None, description="Chức vụ (VD: Nhân viên, Quản lý, Bảo vệ)"),
    expiry_date: Optional[str] = Query(None, description="Ngày hết hạn (ISO 8601, VD: 2027-12-31)"),
):
    parsed_expiry: Optional[datetime] = None
    if expiry_date:
        try:
            parsed_expiry = dateutil_parser.parse(expiry_date)
        except Exception:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=400,
                content={"detail": f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)"},
            )

    user = await User.find_one(User.username == username)
    if user:
        if position is not None:
            user.position = position
        if parsed_expiry is not None:
            user.expiry_date = parsed_expiry
        if position is not None or parsed_expiry is not None:
            await user.save()
        return {
            "created": False,
            "exists": True,
            "has_face": user.face_embedding_b64 is not None,
            "username": username,
            "position": user.position,
            "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "message": (
                f"'{username}' đã tồn tại và đã có khuôn mặt. Chụp lại để cập nhật."
                if user.face_embedding_b64
                else f"'{username}' đã tồn tại, chưa có khuôn mặt. Hãy chụp 3 góc."
            ),
        }
    new_user = User(username=username, hashed_password="", position=position, expiry_date=parsed_expiry)
    await new_user.insert()
    return {
        "created": True,
        "exists": False,
        "has_face": False,
        "username": username,
        "position": position,
        "expiry_date": parsed_expiry.isoformat() if parsed_expiry else None,
        "created_at": None,
        "message": f"✅ Đã tạo user '{username}'. Hãy chụp 3 góc khuôn mặt.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /enroll/reset
# ─────────────────────────────────────────────────────────────────────────────
@router.delete(
    "/reset",
    summary="🔄 Xóa session đăng ký",
    description="Xóa toàn bộ ảnh tạm đã chụp cho username. Bắt đầu lại từ đầu.",
)
def enroll_reset(username: str = Query(..., description="Tên người dùng")):
    if username in _sessions:
        del _sessions[username]
    return {"message": f"Đã reset session cho '{username}'"}
