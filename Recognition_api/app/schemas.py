from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ── Registration ──────────────────────────────────────────────
class RegisterRequest(BaseModel):
    username: str
    password: str


class RegisterResponse(BaseModel):
    message: str
    username: str


# ── Login (password-based) ────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Face verification ─────────────────────────────────────────
class FaceVerifyResponse(BaseModel):
    username: Optional[str]
    matched: bool
    similarity: float
    stream_used: Optional[str]
    message: str


# ── User info ─────────────────────────────────────────────────
class UserInfo(BaseModel):
    id: Optional[str] = None
    username: str
    position: Optional[str] = None
    expiry_date: Optional[datetime] = None
    has_face: bool
    face_image_path: Optional[str] = None
    card_id: Optional[str] = None
    finger_id: Optional[int] = None
    created_at: Optional[str] = None
