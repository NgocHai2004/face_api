from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os
import cv2
import platform

from app.database import init_db
from app.routers import auth as auth_router
from app.routers import stream as stream_router
from app.routers import enroll3 as enroll3_router
from app.routers import verify3 as verify3_router
from app.routers import enroll_manual as enroll_manual_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print(f"[DB] Đã khởi tạo database")
    streams = settings.get_rtsp_streams()
    print(f"[RTSP] {len(streams)} stream: {streams}")
    yield


app = FastAPI(
    title="Face Recognition API",
    description=(
        "## API Nhận diện khuôn mặt – InsightFace + RTSP\n\n"
        "### 🎯 Đăng ký khuôn mặt thủ công (3 góc)\n"
        "**Bước 0 – Khởi tạo user:**\n"
        "- `POST /enroll/init-user`      – Kiểm tra & tạo user tự động (1 API duy nhất)\n\n"
        "**Bước 1-3 – Chụp từng góc:**\n"
        "1. `POST /enroll/capture/thang` – Chụp & kiểm tra góc **THẲNG**\n"
        "2. `POST /enroll/capture/trai`  – Chụp & kiểm tra góc **TRÁI**\n"
        "3. `POST /enroll/capture/phai`  – Chụp & kiểm tra góc **PHẢI**\n"
        "4. `POST /enroll/save`          – Xác nhận đủ 3 góc → **lưu vào DB**\n\n"
        "> Mỗi API chụp trả về `success`, `detected_angle`, `face_crop_b64`, `missing_angles`.\n"
        "> Nếu sai góc, gọi lại đến khi `success: true` rồi mới chuyển góc tiếp.\n\n"
        "### 🔍 Xác thực khuôn mặt\n"
        "- `POST /verify` – Xác thực 1 lần qua RTSP (username hoặc tìm toàn bộ DB)\n\n"
        "### 👥 Quản lý người dùng\n"
        "- `GET /users`                   – Danh sách users\n"
        "- `PATCH /users/{username}`       – Đổi tên / cập nhật ảnh\n"
        "- `DELETE /users/{username}`      – Xóa 1 user\n"
        "- `DELETE /users`                 – Xóa tất cả\n\n"
        "### 📋 Tiện ích đăng ký\n"
        "- `GET /enroll/status`   – Xem góc đã chụp\n"
        "- `DELETE /enroll/reset` – Xóa session\n"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# Mount static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth_router.router, tags=["Face"])
app.include_router(stream_router.router, tags=["Stream"])
app.include_router(enroll3_router.router, tags=["Enroll 3 Angles"])
app.include_router(verify3_router.router, tags=["Verify 3 Angles"])
app.include_router(enroll_manual_router.router, prefix="/enroll", tags=["Enroll Manual"])


@app.get("/", tags=["UI"])
def index():
    """Serve giao diện web HTML/CSS/JS"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health", tags=["Health"])
def health():
    return {
        "status": "ok",
        "rtsp_streams": settings.get_rtsp_streams(),
        "face_threshold": settings.FACE_THRESHOLD,
    }


@app.get("/api/hardware/status", tags=["Hardware"])
def hardware_status():
    """Trả về trạng thái phần cứng: camera cục bộ, RTSP, hệ thống."""
    cameras = []
    for idx in range(3):
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cameras.append({"index": idx, "resolution": f"{w}x{h}", "available": True})
            cap.release()
        else:
            cap.release()

    rtsp_streams = settings.get_rtsp_streams()
    return {
        "status": "ok",
        "platform": platform.machine(),
        "python": platform.python_version(),
        "cameras": cameras,
        "camera_count": len(cameras),
        "rtsp_streams": rtsp_streams,
        "rtsp_count": len(rtsp_streams),
        "face_threshold": settings.FACE_THRESHOLD,
        "insightface_model": settings.INSIGHTFACE_MODEL,
    }
