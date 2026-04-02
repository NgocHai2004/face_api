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
    await init_db()
    print(f"[DB] Đã kết nối MongoDB")
    streams = settings.get_rtsp_streams()
    print(f"[RTSP] {len(streams)} stream: {streams}")
    yield


app = FastAPI(
    title="Face Recognition API",
    description=(
        "## API Nhận diện khuôn mặt – InsightFace + RTSP\n\n"
        "---\n\n"
        "### 📡 Realtime Socket Events → App_center\n"
        "Đăng ký và xác thực tự động push sự kiện lên `App_center` qua WebSocket.\n\n"
        "> **Hub endpoint:** `ws://localhost:8000/ws/producer`  \n"
        "> **type:** `face_recognition` → **topic:** `security` | **priority:** `high`\n\n"
        "| Sự kiện | `event` | Khi nào bắn | Socket? |\n"
        "|---------|---------|-------------|--------|\n"
        "| Chụp 1 góc đăng ký | `enroll3_angle` | Mỗi khi chụp thành công 1 góc | ✅ |\n"
        "| Hoàn thành đăng ký | `enroll3_done` | Sau khi lưu embedding 3 góc vào DB | ✅ |\n"
        "| Xác thực khớp | `verify_matched` | Có mặt + score ≥ 0.6 | ✅ |\n"
        "| Xác thực không khớp | `verify_unmatched` | Có mặt + score < 0.6 | ✅ |\n"
        "| Không có mặt | `verify_no_face` | Không phát hiện khuôn mặt trong frame | ❌ |\n\n"
        "---\n\n"
        "### 🎯 Đăng ký khuôn mặt tự động – SSE (3 góc)\n"
        "- `GET /enroll3?username=alice&source=0[&position=NhanVien&expiry_date=2027-12-31T00:00:00]`\n\n"
        "> Mỗi góc chụp xong: trả SSE về client **đồng thời** push socket lên App_center.  \n"
        "> SSE event có `frame_b64` (preview full frame) + `face_crop_b64` (ảnh mặt crop).  \n"
        "> `face_crop_b64` trong socket → Hub lưu file → thay bằng `face_image_url`.\n\n"
        "**SSE — đang chờ đúng góc:**\n"
        "```json\n"
        "{ \"step\": 1, \"total_steps\": 3, \"required_angle\": \"THANG\",\n"
        "  \"direction\": \"TRAI\", \"frame_b64\": \"<base64>\", \"done\": false,\n"
        "  \"message\": \"Bước 1/3 — Cần: THANG, đang: TRAI\" }\n"
        "```\n\n"
        "**SSE — chụp góc thành công (`done: false`):**\n"
        "```json\n"
        "{ \"event\": \"enroll3_angle\", \"step\": 1, \"total_steps\": 3,\n"
        "  \"required_angle\": \"THANG\", \"captured\": \"THANG\",\n"
        "  \"username\": \"alice\", \"position\": \"NhanVien\",\n"
        "  \"expiry_date\": \"2027-12-31T00:00:00\",\n"
        "  \"face_crop_b64\": \"<base64>\", \"frame_b64\": \"<base64>\",\n"
        "  \"timestamp\": \"2026-04-02T10:00:00\", \"done\": false }\n"
        "```\n\n"
        "**SSE — hoàn tất (`done: true`):**\n"
        "```json\n"
        "{ \"event\": \"enroll3_done\", \"done\": true,\n"
        "  \"username\": \"alice\", \"position\": \"NhanVien\",\n"
        "  \"expiry_date\": \"2027-12-31T00:00:00\",\n"
        "  \"angles_captured\": [\"THANG\",\"TRAI\",\"PHAI\"],\n"
        "  \"timestamp\": \"2026-04-02T10:00:05\",\n"
        "  \"message\": \"✅ Đăng ký thành công 3 góc cho 'alice'!\" }\n"
        "```\n\n"
        "---\n\n"
        "### 🔍 Xác thực khuôn mặt liên tục – SSE (1s/lần)\n"
        "- `GET /verify3?source=0[&username=alice]`\n\n"
        "> Mỗi 1 giây: chụp frame → detect → so khớp DB → bắn SSE + push socket.\n\n"
        "**SSE — không có mặt (`phase: no_face`):** — không push socket\n"
        "```json\n"
        "{ \"phase\": \"no_face\", \"event\": \"verify_no_face\",\n"
        "  \"source\": \"0\", \"timestamp\": \"...\",\n"
        "  \"message\": \"Không phát hiện khuôn mặt\" }\n"
        "```\n\n"
        "**SSE — có mặt, không khớp (`phase: scanning`):** — push socket ✅\n"
        "```json\n"
        "{ \"phase\": \"scanning\", \"event\": \"verify_unmatched\",\n"
        "  \"username\": null, \"nearest\": \"alice\", \"nearest_position\": \"NhanVien\",\n"
        "  \"nearest_expiry_date\": \"2027-12-31T00:00:00\",\n"
        "  \"score\": 0.32, \"matched\": false,\n"
        "  \"face_crop_b64\": \"<base64>\", \"frame_b64\": \"<base64>\",\n"
        "  \"timestamp\": \"...\", \"source\": \"0\" }\n"
        "```\n\n"
        "**SSE — xác thực thành công (`phase: matched`):** — push socket ✅\n"
        "```json\n"
        "{ \"phase\": \"matched\", \"event\": \"verify_matched\",\n"
        "  \"username\": \"alice\", \"position\": \"NhanVien\",\n"
        "  \"expiry_date\": \"2027-12-31T00:00:00\",\n"
        "  \"score\": 0.85, \"matched\": true,\n"
        "  \"face_crop_b64\": \"<base64>\", \"frame_b64\": \"<base64>\",\n"
        "  \"timestamp\": \"...\", \"source\": \"0\" }\n"
        "```\n\n"
        "> **NormalizedEvent consumer nhận:** `face_crop_b64` → `face_image_url` + `face_image_path`\n\n"
        "---\n\n"
        "### 🎯 Đăng ký khuôn mặt thủ công (3 góc)\n"
        "**Bước 0 – Khởi tạo user:**\n"
        "- `POST /enroll/init-user?username=alice[&position=NhanVien&expiry_date=2027-12-31T00:00:00]` – Kiểm tra & tạo user\n\n"
        "**Bước 1-3 – Chụp từng góc:**\n"
        "1. `POST /enroll/capture/thang?username=alice&source=0`\n"
        "2. `POST /enroll/capture/trai?username=alice&source=0`\n"
        "3. `POST /enroll/capture/phai?username=alice&source=0`\n"
        "4. `POST /enroll/save?username=alice[&position=NhanVien&expiry_date=2027-12-31T00:00:00]` – Lưu vào DB\n\n"
        "> Mỗi API chụp trả về `success`, `detected_angle`, `face_crop_b64`, `missing_angles`.  \n"
        "> Nếu sai góc → gọi lại đến khi `success: true` rồi mới chuyển góc tiếp.\n\n"
        "---\n\n"
        "### 👥 Quản lý người dùng\n"
        "- `GET /users`                                                                   – Danh sách users (trả về `expiry_date`)\n"
        "- `PATCH /users/{username}[?new_username=x&position=y&expiry_date=2027-12-31]`  – Đổi tên / chức vụ / ngày hết hạn / ảnh\n"
        "- `DELETE /users/{username}`                                                     – Xóa 1 user\n"
        "- `DELETE /users`                                                                – Xóa tất cả\n\n"
        "> **`expiry_date`**: ISO 8601 string, VD `2027-12-31` hoặc `2027-12-31T00:00:00`.  \n"
        "> Truyền `expiry_date=null` để xóa ngày hết hạn.  \n"
        "> Trường `expiry_date` có trong tất cả response đăng ký, xác thực và danh sách user.\n\n"
        "### 📋 Tiện ích đăng ký thủ công\n"
        "- `GET /enroll/status?username=alice`   – Xem góc đã chụp\n"
        "- `DELETE /enroll/reset?username=alice` – Xóa session\n"
    ),
    version="2.3.0",
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
