"""
Image Store - Lưu face crop base64 thành file ảnh trên mount storage
"""
import base64
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("event_hub.image_store")

# Thư mục lưu trữ: đọc từ env FACE_STORAGE_PATH, fallback về /mnt/faces
FACE_STORAGE_PATH = Path(os.getenv("FACE_STORAGE_PATH", "/mnt/faces"))


def _get_local_ip() -> str:
    """Detect IP LAN thực của máy"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def get_hub_base_url() -> str:
    """
    Lấy base URL của Hub để tạo face_image_url.
    Ưu tiên: env HUB_BASE_URL → auto-detect LAN IP
    """
    from_env = os.getenv("HUB_BASE_URL", "").strip()
    if from_env:
        return from_env.rstrip("/")
    port = os.getenv("APP_PORT", "8000")
    return f"http://{_get_local_ip()}:{port}"


def _ensure_dir() -> Path:
    """Tạo thư mục nếu chưa tồn tại"""
    FACE_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    return FACE_STORAGE_PATH


def save_face_crop(base64_str: str | None, prefix: str = "face") -> str | None:
    """
    Nhận chuỗi base64 ảnh, lưu thành file .jpg trên mount storage.

    Returns:
        str: đường dẫn file tuyệt đối (vd: /mnt/faces/face_20260329_105417_abc123.jpg)
        None: nếu base64_str là None hoặc rỗng
    """
    if not base64_str:
        return None

    try:
        # Xóa data URI prefix nếu có (vd: "data:image/jpeg;base64,...")
        if "," in base64_str:
            base64_str = base64_str.split(",", 1)[1]

        image_bytes = base64.b64decode(base64_str)

        # Tên file: prefix_YYYYMMDD_HHMMSS_microsec.jpg
        now = datetime.now(timezone.utc)
        filename = f"{prefix}_{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond:06d}.jpg"

        storage_dir = _ensure_dir()
        file_path = storage_dir / filename

        with open(file_path, "wb") as f:
            f.write(image_bytes)

        logger.info("Face image saved: %s (%d bytes)", file_path, len(image_bytes))
        return str(file_path)

    except Exception as e:
        logger.warning("Failed to save face image: %s", e)
        return None


def get_image_url(file_path: str | None, base_url: str) -> str | None:
    """
    Chuyển đổi file path thành HTTP URL để consumer truy cập ảnh.

    Args:
        file_path: /mnt/faces/face_20260329_105417_abc123.jpg
        base_url:  http://192.168.21.47:8000

    Returns:
        http://192.168.21.47:8000/faces/face_20260329_105417_abc123.jpg
    """
    if not file_path:
        return None
    filename = Path(file_path).name
    return f"{base_url}/faces/{filename}"
