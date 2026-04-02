from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import numpy as np
import cv2
import asyncio
import os
import glob
from dateutil import parser as dateutil_parser

from app.database import User
from app.face_utils import (
    extract_embedding_from_image,
    embedding_to_bytes, bytes_to_embedding,
    verify_faces, save_face_image, crop_to_base64,
)
from app.rtsp_utils import capture_frame_from_rtsp
from app.config import settings
from app.ws_producer import push_event_async

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────
class RegisterResponse(BaseModel):
    success: bool
    username: str
    message: str


class VerifyResponse(BaseModel):
    username: Optional[str]
    matched: bool
    similarity: float
    face_crop_base64: Optional[str]
    timestamp: str
    rtsp_url: str
    message: str
    expiry_date: Optional[str] = None
    position: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# POST /register-from-camera
# ─────────────────────────────────────────────────────────────
@router.post("/register-from-camera", response_model=RegisterResponse,
             summary="Đăng ký khuôn mặt từ camera")
async def register_from_camera(
    username: str = Query(..., description="Tên định danh người dùng"),
    rtsp_url: str = Query(..., description="RTSP URL hoặc index camera (0 = webcam local)"),
    position: Optional[str] = Query(None, description="Chức vụ (VD: Nhân viên, Quản lý, Bảo vệ)"),
    expiry_date: Optional[str] = Query(None, description="Ngày hết hạn (ISO 8601, VD: 2027-12-31 hoặc 2027-12-31T00:00:00)"),
):
    frame = capture_frame_from_rtsp(rtsp_url)
    if frame is None:
        raise HTTPException(status_code=503, detail=f"Không thể lấy frame từ: {rtsp_url}")

    embedding, _ = extract_embedding_from_image(frame)
    if embedding is None:
        raise HTTPException(status_code=400, detail="Không phát hiện khuôn mặt trong frame camera")

    parsed_expiry: Optional[datetime] = None
    if expiry_date:
        try:
            parsed_expiry = dateutil_parser.parse(expiry_date)
        except Exception:
            raise HTTPException(status_code=400, detail=f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)")

    user = await User.find_one(User.username == username)
    if user:
        user.face_embedding = embedding_to_bytes(embedding)
        user.face_image_path = save_face_image(username, frame)
        if position is not None:
            user.position = position
        if parsed_expiry is not None:
            user.expiry_date = parsed_expiry
        await user.save()
        return RegisterResponse(success=True, username=username,
                                message="Cập nhật khuôn mặt từ camera thành công")

    image_path = save_face_image(username, frame)
    user = User(
        username=username,
        hashed_password="",
        position=position,
        expiry_date=parsed_expiry,
        face_embedding_b64=None,
        face_image_path=image_path,
    )
    user.face_embedding = embedding_to_bytes(embedding)
    await user.insert()
    return RegisterResponse(success=True, username=username,
                            message="Đăng ký khuôn mặt từ camera thành công")


# ─────────────────────────────────────────────────────────────
# POST /register
# ─────────────────────────────────────────────────────────────
@router.post("/register", response_model=RegisterResponse, summary="Đăng ký khuôn mặt")
async def register(
    username: str = Query(..., description="Tên định danh người dùng"),
    position: Optional[str] = Query(None, description="Chức vụ (VD: Nhân viên, Quản lý, Bảo vệ)"),
    expiry_date: Optional[str] = Query(None, description="Ngày hết hạn (ISO 8601, VD: 2027-12-31 hoặc 2027-12-31T00:00:00)"),
    face_image: UploadFile = File(..., description="Ảnh khuôn mặt"),
):
    contents = await face_image.read()
    nparr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Không thể đọc ảnh tải lên")

    embedding, _ = extract_embedding_from_image(img)
    if embedding is None:
        raise HTTPException(status_code=400, detail="Không phát hiện khuôn mặt trong ảnh")

    parsed_expiry: Optional[datetime] = None
    if expiry_date:
        try:
            parsed_expiry = dateutil_parser.parse(expiry_date)
        except Exception:
            raise HTTPException(status_code=400, detail=f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)")

    user = await User.find_one(User.username == username)
    if user:
        user.face_embedding = embedding_to_bytes(embedding)
        user.face_image_path = save_face_image(username, img)
        if position is not None:
            user.position = position
        if parsed_expiry is not None:
            user.expiry_date = parsed_expiry
        await user.save()
        return RegisterResponse(success=True, username=username,
                                message="Cập nhật khuôn mặt thành công")

    image_path = save_face_image(username, img)
    user = User(username=username, hashed_password="", position=position,
                expiry_date=parsed_expiry, face_image_path=image_path)
    user.face_embedding = embedding_to_bytes(embedding)
    await user.insert()
    return RegisterResponse(success=True, username=username,
                            message="Đăng ký khuôn mặt thành công")


# ─────────────────────────────────────────────────────────────
# POST /verify
# ─────────────────────────────────────────────────────────────
@router.post("/verify", response_model=VerifyResponse, summary="Xác thực khuôn mặt qua RTSP URL")
async def verify(
    rtsp_url: str = Query(..., description="URL RTSP của camera"),
    username: Optional[str] = Query(None,
                                    description="Username cần xác thực (để trống = tìm toàn bộ DB)"),
):
    timestamp = datetime.now().isoformat()

    frame = capture_frame_from_rtsp(rtsp_url)
    if frame is None:
        raise HTTPException(status_code=503, detail=f"Không thể lấy frame từ: {rtsp_url}")

    query_embedding, face_crop = extract_embedding_from_image(frame)
    face_b64 = crop_to_base64(face_crop) if face_crop is not None else None

    if query_embedding is None:
        result = VerifyResponse(
            username=None, matched=False, similarity=0.0,
            face_crop_base64=None, timestamp=timestamp,
            rtsp_url=rtsp_url,
            message="Không phát hiện khuôn mặt trong frame RTSP",
        )
        asyncio.ensure_future(push_event_async(result.model_dump()))
        return result

    # ── Xác thực 1 user cụ thể ──────────────────────────────
    if username:
        user = await User.find_one(User.username == username)
        if not user or not user.face_embedding:
            raise HTTPException(status_code=404,
                                detail=f"User '{username}' chưa đăng ký khuôn mặt")
        stored = bytes_to_embedding(user.face_embedding)
        matched, score = verify_faces(stored, query_embedding)
        result = VerifyResponse(
            username=username, matched=matched,
            similarity=round(score, 4),
            face_crop_base64=face_b64,
            timestamp=timestamp,
            rtsp_url=rtsp_url,
            message="Khuôn mặt khớp!" if matched else "Khuôn mặt không khớp",
            expiry_date=user.expiry_date.isoformat() if user.expiry_date else None,
            position=user.position,
        )
        asyncio.ensure_future(push_event_async(result.model_dump()))
        return result

    # ── Tìm toàn bộ DB ───────────────────────────────────────
    users = await User.find(User.face_embedding_b64 != None).to_list()  # noqa: E711
    if not users:
        raise HTTPException(status_code=404, detail="Chưa có khuôn mặt nào được đăng ký")

    best_user, best_score = None, -1.0
    for u in users:
        stored = bytes_to_embedding(u.face_embedding)
        _, score = verify_faces(stored, query_embedding)
        if score > best_score:
            best_score = score
            best_user = u

    matched = best_score >= settings.FACE_THRESHOLD
    result = VerifyResponse(
        username=best_user.username if matched else None,
        matched=matched,
        similarity=round(best_score, 4),
        face_crop_base64=face_b64,
        timestamp=timestamp,
        rtsp_url=rtsp_url,
        message=f"Nhận diện: {best_user.username}" if matched else "Không tìm thấy khuôn mặt khớp",
        expiry_date=best_user.expiry_date.isoformat() if best_user.expiry_date else None,
        position=best_user.position,
    )
    asyncio.ensure_future(push_event_async(result.model_dump()))
    return result


# ─────────────────────────────────────────────────────────────
# GET /users
# ─────────────────────────────────────────────────────────────
@router.get(
    "/users",
    summary="Liệt kê tất cả người dùng",
    description=(
        "Trả về danh sách toàn bộ người dùng trong DB.\n\n"
        "Mỗi user có:\n"
        "- `id` — MongoDB ObjectId (string)\n"
        "- `username` — tên định danh\n"
        "- `position` — chức vụ (bắt buộc khi đăng ký)\n"
        "- `has_face` — `true` nếu đã đăng ký khuôn mặt\n"
        "- `face_image_path` — đường dẫn ảnh trên server\n"
        "- `created_at` — ISO 8601\n"
    ),
    responses={
        200: {
            "description": "Danh sách users",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": "660e8400-e29b-41d4-a716-446655440000",
                            "username": "nguyen_van_a",
                            "position": "Nhân viên",
                            "has_face": True,
                            "face_image_path": "./face_images/nguyen_van_a_THANG.jpg",
                            "created_at": "2026-04-02T10:00:00",
                        }
                    ]
                }
            },
        }
    },
)
async def list_users():
    users = await User.find_all().to_list()
    return [
        {
            "id": str(u.id),
            "username": u.username,
            "position": u.position,
            "expiry_date": u.expiry_date.isoformat() if u.expiry_date else None,
            "has_face": u.face_embedding_b64 is not None,
            "face_image_path": u.face_image_path,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


# ─────────────────────────────────────────────────────────────
# PATCH /users/{username}
# ─────────────────────────────────────────────────────────────
@router.patch(
    "/users/{username}",
    summary="Sửa tên, chức vụ hoặc cập nhật ảnh khuôn mặt",
    description=(
        "Cập nhật thông tin người dùng. Có thể cập nhật **một hoặc nhiều** trường cùng lúc:\n\n"
        "| Query Param | Mô tả |\n"
        "|-------------|-------|\n"
        "| `new_username` | Tên mới — để trống nếu không đổi |\n"
        "| `position` | Chức vụ mới — để trống nếu không đổi |\n"
        "| `face_image` | Ảnh mặt mới (multipart/form-data) — không gửi nếu không đổi |\n\n"
        "**Ít nhất 1 trường phải được cung cấp**, nếu không → 400.\n\n"
        "Khi đổi tên: ảnh trên disk cũng được đổi tên theo.\n"
        "Khi cập nhật ảnh: hệ thống tự extract embedding mới từ ảnh tải lên."
    ),
    responses={
        200: {
            "description": "Cập nhật thành công",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Đã đổi tên, cập nhật chức vụ cho user 'nguyen_van_a'",
                        "username": "nguyen_van_b",
                        "position": "Quản lý",
                        "changes": ["đổi tên", "cập nhật chức vụ"],
                    }
                }
            },
        },
        400: {"description": "Không có thay đổi nào / username mới đã tồn tại / ảnh không hợp lệ"},
        404: {"description": "User không tồn tại"},
    },
)
async def update_user(
    username: str,
    new_username: Optional[str] = Query(None, description="Tên mới (để trống nếu không đổi)"),
    position: Optional[str] = Query(None, description="Chức vụ mới (để trống nếu không đổi)"),
    expiry_date: Optional[str] = Query(None, description="Ngày hết hạn mới — ISO 8601 (VD: 2027-12-31). Gửi 'null' để xóa ngày hết hạn."),
    face_image: UploadFile = File(None, description="Ảnh khuôn mặt mới (multipart, để trống nếu không đổi)"),
):
    user = await User.find_one(User.username == username)
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' không tồn tại")

    changes = []

    # Đổi tên
    if new_username and new_username != username:
        existing = await User.find_one(User.username == new_username)
        if existing:
            raise HTTPException(status_code=400,
                                detail=f"Username '{new_username}' đã tồn tại")
        for old_path in glob.glob(os.path.join(settings.FACE_IMAGES_DIR, f"{username}*")):
            ext = os.path.basename(old_path).replace(username, "")
            new_path = os.path.join(settings.FACE_IMAGES_DIR, f"{new_username}{ext}")
            try:
                os.rename(old_path, new_path)
            except OSError:
                pass
        if user.face_image_path:
            user.face_image_path = user.face_image_path.replace(username, new_username)
        user.username = new_username
        changes.append("đổi tên")

    # Cập nhật chức vụ
    if position is not None:
        if not position.strip():
            raise HTTPException(status_code=400, detail="position không được để trống")
        user.position = position.strip()
        changes.append("cập nhật chức vụ")

    # Cập nhật ngày hết hạn
    if expiry_date is not None:
        if expiry_date.strip().lower() == "null":
            user.expiry_date = None
            changes.append("xóa ngày hết hạn")
        else:
            try:
                user.expiry_date = dateutil_parser.parse(expiry_date.strip())
                changes.append("cập nhật ngày hết hạn")
            except Exception:
                raise HTTPException(status_code=400, detail=f"expiry_date không hợp lệ: '{expiry_date}'. Dùng định dạng ISO 8601 (VD: 2027-12-31)")

    # Cập nhật ảnh khuôn mặt
    if face_image and face_image.filename:
        contents = await face_image.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(status_code=400, detail="Không đọc được ảnh")
        embedding, _ = extract_embedding_from_image(img)
        if embedding is None:
            raise HTTPException(status_code=400, detail="Không phát hiện khuôn mặt trong ảnh")
        current_name = user.username
        user.face_embedding = embedding_to_bytes(embedding)
        user.face_image_path = save_face_image(current_name, img)
        changes.append("cập nhật ảnh")

    if not changes:
        raise HTTPException(status_code=400, detail="Không có thay đổi nào được cung cấp (new_username / position / face_image)")

    await user.save()
    return {
        "message": f"Đã {', '.join(changes)} cho user '{username}'",
        "username": user.username,
        "position": user.position,
        "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
        "changes": changes,
    }


# ─────────────────────────────────────────────────────────────
# DELETE /users/{username}
# ─────────────────────────────────────────────────────────────
@router.delete(
    "/users/{username}",
    summary="Xóa người dùng theo username",
    description=(
        "Xóa user khỏi MongoDB **và** xóa toàn bộ ảnh khuôn mặt trên disk.\n\n"
        "⚠️ Hành động **không thể hoàn tác**."
    ),
    responses={
        200: {
            "description": "Xóa thành công",
            "content": {
                "application/json": {
                    "example": {"message": "Đã xóa user 'nguyen_van_a'", "username": "nguyen_van_a"}
                }
            },
        },
        404: {"description": "User không tồn tại"},
    },
)
async def delete_user(username: str):
    user = await User.find_one(User.username == username)
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' không tồn tại")
    for path in glob.glob(os.path.join(settings.FACE_IMAGES_DIR, f"{username}*")):
        try:
            os.remove(path)
        except OSError:
            pass
    await user.delete()
    return {"message": f"Đã xóa user '{username}'", "username": username}


# ─────────────────────────────────────────────────────────────
# DELETE /users
# ─────────────────────────────────────────────────────────────
@router.delete(
    "/users",
    summary="Xóa toàn bộ người dùng",
    description=(
        "Xóa **tất cả** người dùng khỏi MongoDB và toàn bộ ảnh khuôn mặt trên disk.\n\n"
        "⚠️ **Hành động không thể hoàn tác.** Nên dùng `DELETE /users/{username}` nếu chỉ xóa 1 người."
    ),
    responses={
        200: {
            "description": "Xóa thành công",
            "content": {
                "application/json": {
                    "example": {"message": "Đã xóa 5 người dùng", "deleted_count": 5}
                }
            },
        }
    },
)
async def delete_all_users():
    users = await User.find_all().to_list()
    count = len(users)
    await User.find_all().delete()
    for path in glob.glob(os.path.join(settings.FACE_IMAGES_DIR, "*.jpg")):
        try:
            os.remove(path)
        except OSError:
            pass
    return {"message": f"Đã xóa {count} người dùng", "deleted_count": count}
