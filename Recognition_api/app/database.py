"""
app/database.py

MongoDB + Beanie ODM – thay thế hoàn toàn SQLAlchemy / SQLite.

Collections:
  • users  → UserDocument
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Optional

from beanie import Document, Indexed, init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import Field

from app.config import settings


# ── ODM Document ──────────────────────────────────────────────────────────────

class UserDocument(Document):
    """Lưu thông tin người dùng và embedding khuôn mặt."""

    username: Indexed(str, unique=True)          # type: ignore[valid-type]
    hashed_password: str = ""
    position: Optional[str] = None               # chức vụ: VD "Nhân viên", "Quản lý", "Bảo vệ"...
    expiry_date: Optional[datetime] = None       # ngày hết hạn truy cập
    # face_embedding lưu dưới dạng base64 string (thay cho LargeBinary của SQLite)
    face_embedding_b64: Optional[str] = None
    face_image_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "users"                           # tên collection trong MongoDB
        use_state_management = True

    # ── helpers tương thích ngược với code cũ ─────────────────────────────────

    @property
    def face_embedding(self) -> Optional[bytes]:
        """Trả về bytes giống LargeBinary cũ."""
        if self.face_embedding_b64 is None:
            return None
        return base64.b64decode(self.face_embedding_b64)

    @face_embedding.setter
    def face_embedding(self, value: Optional[bytes]) -> None:
        if value is None:
            self.face_embedding_b64 = None
        else:
            self.face_embedding_b64 = base64.b64encode(value).decode()

    # SQLAlchemy trả về integer id; MongoDB dùng ObjectId.
    # Giữ lại trường id dưới dạng string để không phá vỡ các serialization cũ.
    @property
    def id_str(self) -> str:
        return str(self.id)


# Alias để code cũ vẫn dùng được tên "User"
User = UserDocument


# ── Khởi tạo kết nối ──────────────────────────────────────────────────────────

async def init_db() -> None:
    """Gọi 1 lần trong lifespan của FastAPI."""
    # beanie v2: truyền connection_string thay vì database object
    await init_beanie(
        connection_string=f"{settings.MONGODB_URL}/{settings.MONGODB_DB_NAME}",
        document_models=[UserDocument],
    )
    print(f"[MongoDB] Kết nối tới '{settings.MONGODB_URL}', db='{settings.MONGODB_DB_NAME}'")


# ── get_db dependency (async) ─────────────────────────────────────────────────
# Beanie hoạt động với motor (async) nên không cần session truyền thống.
# Các router dùng Depends(get_db) vẫn hoạt động — hàm trả về None, 
# router sẽ gọi trực tiếp UserDocument.find / .save / .delete.

async def get_db():  # noqa: ANN201
    """Dependency placeholder – giữ lại chữ ký cũ, không làm gì."""
    return None
