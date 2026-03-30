from pydantic import BaseModel
from typing import Optional


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
