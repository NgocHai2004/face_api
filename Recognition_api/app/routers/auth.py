from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
import numpy as np
import cv2
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.database import get_db, User
from app.face_utils import (
    extract_embedding_from_image,
    embedding_to_bytes, bytes_to_embedding,
    verify_faces, save_face_image, crop_to_base64,
)
from app.rtsp_utils import capture_frame_from_rtsp
from app.config import settings
import asyncio
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
    face_crop_base64: Optional[str]   # JPEG ảnh mặt cắt, base64
    timestamp: str                    # ISO 8601
    rtsp_url: str
    message: str


# ─────────────────────────────────────────────────────────────
# POST /register-from-camera
# Chụp frame từ RTSP/camera index và đăng ký khuôn mặt
# ─────────────────────────────────────────────────────────────
@router.post("/register-from-camera", response_model=RegisterResponse, summary="Đăng ký khuôn mặt từ camera")
def register_from_camera(
    username: str = Query(..., description="Tên định danh người dùng"),
    rtsp_url: str = Query(..., description="RTSP URL hoặc index camera (0 = webcam local)"),
    db: Session = Depends(get_db),
):
    frame = capture_frame_from_rtsp(rtsp_url)
    if frame is None:
        raise HTTPException(status_code=503, detail=f"Không thể lấy frame từ: {rtsp_url}")

    embedding, _ = extract_embedding_from_image(frame)
    if embedding is None:
        raise HTTPException(status_code=400, detail="Không phát hiện khuôn mặt trong frame camera")

    user = db.query(User).filter(User.username == username).first()
    if user:
        user.face_embedding = embedding_to_bytes(embedding)
        user.face_image_path = save_face_image(username, frame)
        db.commit()
        return RegisterResponse(success=True, username=username, message="Cập nhật khuôn mặt từ camera thành công")

    image_path = save_face_image(username, frame)
    user = User(
        username=username,
        hashed_password="",
        face_embedding=embedding_to_bytes(embedding),
        face_image_path=image_path,
    )
    db.add(user)
    db.commit()
    return RegisterResponse(success=True, username=username, message="Đăng ký khuôn mặt từ camera thành công")


# ─────────────────────────────────────────────────────────────
# POST /register
# ─────────────────────────────────────────────────────────────
@router.post("/register", response_model=RegisterResponse, summary="Đăng ký khuôn mặt")
async def register(
    username: str = Query(..., description="Tên định danh người dùng"),
    face_image: UploadFile = File(..., description="Ảnh khuôn mặt"),
    db: Session = Depends(get_db),
):
    contents = await face_image.read()
    nparr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Không thể đọc ảnh tải lên")

    embedding, _ = extract_embedding_from_image(img)
    if embedding is None:
        raise HTTPException(status_code=400, detail="Không phát hiện khuôn mặt trong ảnh")

    user = db.query(User).filter(User.username == username).first()
    if user:
        user.face_embedding = embedding_to_bytes(embedding)
        user.face_image_path = save_face_image(username, img)
        db.commit()
        return RegisterResponse(success=True, username=username, message="Cập nhật khuôn mặt thành công")

    image_path = save_face_image(username, img)
    user = User(
        username=username,
        hashed_password="",
        face_embedding=embedding_to_bytes(embedding),
        face_image_path=image_path,
    )
    db.add(user)
    db.commit()
    return RegisterResponse(success=True, username=username, message="Đăng ký khuôn mặt thành công")


# ─────────────────────────────────────────────────────────────
# POST /verify
# ─────────────────────────────────────────────────────────────
@router.post("/verify", response_model=VerifyResponse, summary="Xác thực khuôn mặt qua RTSP URL")
async def verify(
    rtsp_url: str = Query(..., description="URL RTSP của camera"),
    username: Optional[str] = Query(None, description="Username cần xác thực (để trống = tìm toàn bộ DB)"),
    db: Session = Depends(get_db),
):
    timestamp = datetime.now().isoformat()

    # Chụp frame từ RTSP
    frame = capture_frame_from_rtsp(rtsp_url)
    if frame is None:
        raise HTTPException(status_code=503, detail=f"Không thể lấy frame từ: {rtsp_url}")

    # Trích xuất embedding + crop khuôn mặt
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
        user = db.query(User).filter(User.username == username).first()
        if not user or not user.face_embedding:
            raise HTTPException(status_code=404, detail=f"User '{username}' chưa đăng ký khuôn mặt")
        stored = bytes_to_embedding(user.face_embedding)
        matched, score = verify_faces(stored, query_embedding)
        result = VerifyResponse(
            username=username, matched=matched,
            similarity=round(score, 4),
            face_crop_base64=face_b64,
            timestamp=timestamp,
            rtsp_url=rtsp_url,
            message="Khuôn mặt khớp!" if matched else "Khuôn mặt không khớp",
        )
        asyncio.ensure_future(push_event_async(result.model_dump()))
        return result

    # ── Tìm toàn bộ DB ───────────────────────────────────────
    users = db.query(User).filter(User.face_embedding.isnot(None)).all()
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
    )
    asyncio.ensure_future(push_event_async(result.model_dump()))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /users  — Liệt kê tất cả user trong DB
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/users", summary="Liệt kê tất cả người dùng")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "has_face": u.face_embedding is not None,
            "face_image_path": u.face_image_path,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /users/{username}  — Đổi tên và/hoặc cập nhật ảnh khuôn mặt
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/users/{username}", summary="Sửa tên user hoặc cập nhật ảnh khuôn mặt")
async def update_user(
    username: str,
    new_username: Optional[str] = Query(None, description="Tên mới (để trống nếu không đổi)"),
    face_image: UploadFile = File(None, description="Ảnh khuôn mặt mới (để trống nếu không đổi)"),
    db: Session = Depends(get_db),
):
    import os, glob
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' không tồn tại")

    changes = []

    # Đổi tên
    if new_username and new_username != username:
        if db.query(User).filter(User.username == new_username).first():
            raise HTTPException(status_code=400, detail=f"Username '{new_username}' đã tồn tại")
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
        raise HTTPException(status_code=400, detail="Không có thay đổi nào được cung cấp")

    db.commit()
    return {
        "message": f"Đã {', '.join(changes)} cho user '{username}'",
        "username": user.username,
        "changes": changes,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /users/{username}  — Xóa 1 user theo username
# ─────────────────────────────────────────────────────────────────────────────
@router.delete("/users/{username}", summary="Xóa người dùng theo username")
def delete_user(username: str, db: Session = Depends(get_db)):
    import os, glob
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' không tồn tại")
    for path in glob.glob(os.path.join(settings.FACE_IMAGES_DIR, f"{username}*")):
        try:
            os.remove(path)
        except OSError:
            pass
    db.delete(user)
    db.commit()
    return {"message": f"Đã xóa user '{username}'", "username": username}


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /users  — Xóa toàn bộ user
# ─────────────────────────────────────────────────────────────────────────────
@router.delete("/users", summary="Xóa toàn bộ người dùng")
def delete_all_users(db: Session = Depends(get_db)):
    import os, glob
    count = db.query(User).count()
    db.query(User).delete()
    db.commit()
    for path in glob.glob(os.path.join(settings.FACE_IMAGES_DIR, "*.jpg")):
        try:
            os.remove(path)
        except OSError:
            pass
    return {"message": f"Đã xóa {count} người dùng", "deleted_count": count}
