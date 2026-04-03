"""
app/enroll_state.py

Shared enrollment state — dùng để verify3 biết khi nào có phiên đăng ký đang chạy
và tự tạm dừng xác thực trong suốt phiên đó.

Usage:
    from app.enroll_state import enroll_state

    # Bắt đầu phiên đăng ký
    enroll_state.start("alice")

    # Kết thúc phiên đăng ký
    enroll_state.finish("alice")

    # Kiểm tra có phiên nào đang chạy không
    if enroll_state.is_active():
        ...  # đang đăng ký, bỏ qua xác thực
"""

import threading


class EnrollState:
    """Thread-safe set of usernames currently in an active enrollment session."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active: set[str] = set()

    # ── Bắt đầu phiên đăng ký cho username ──────────────────────────────────
    def start(self, username: str) -> None:
        with self._lock:
            self._active.add(username)

    # ── Kết thúc phiên đăng ký cho username ─────────────────────────────────
    def finish(self, username: str) -> None:
        with self._lock:
            self._active.discard(username)

    # ── Kiểm tra có ÍT NHẤT 1 phiên đăng ký đang chạy không ────────────────
    def is_active(self) -> bool:
        with self._lock:
            return bool(self._active)

    # ── Danh sách username đang đăng ký (debug) ──────────────────────────────
    def active_users(self) -> list[str]:
        with self._lock:
            return list(self._active)


# Singleton dùng chung cho toàn bộ app
enroll_state = EnrollState()
