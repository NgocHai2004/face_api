from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
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
from app.routers import enroll_nfc as enroll_nfc_router
from app.routers import verify_card as verify_card_router
from app.routers import enroll_finger as enroll_finger_router
from app.routers import verify_finger as verify_finger_router
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
        "## API Nhận diện khuôn mặt – InsightFace + RTSP + NFC\n\n"
        "---\n\n"
        "### 🚀 Đăng ký NFC + Khuôn mặt – **2 API** (tự động)\n\n"
        "**Điều kiện thành công:** đủ 3 góc mặt **HOẶC** có thẻ NFC (hoặc cả hai).\n\n"
        "**Bước 1 – Bắt đầu stream đăng ký (SSE):**\n"
        "```\n"
        "GET /enroll/nfc/stream?username=alice&position=NhanVien&source=0\n"
        "```\n"
        "→ Tự tạo user + session, stream camera tự nhận diện 3 góc mặt.\n"
        "SSE events: `session_created` → `stream_started` → `angle_instruction`"
        " → `enroll_nfc_angle` → `nfc_scanned` → `face_complete` → `stream_ended`\n\n"
        "**Song song – NFC reader gửi thẻ (module-rfid-nfc):**\n"
        "```\n"
        "POST /enroll/nfc/card?username=alice&card_id=A66AB0AA\n"
        "```\n\n"
        "**Bước 2 – Kết thúc phiên + lưu DB:**\n"
        "```\n"
        "POST /enroll/nfc/finish?username=alice\n"
        "```\n"
        "Response thành công:\n"
        "```json\n"
        "{ \"success\": true, \"face_ok\": true, \"card_ok\": true,\n"
        "  \"card_id\": \"A66AB0AA\", \"angles_captured\": [\"THANG\",\"TRAI\",\"PHAI\"],\n"
        "  \"registered_with\": \"khuôn mặt 3 góc + thẻ NFC (A66AB0AA)\" }\n"
        "```\n\n"
        "**Tiện ích:**\n"
        "- `GET  /enroll/nfc/session-status?username=alice` – Xem trạng thái session\n"
        "- `DELETE /enroll/nfc/reset?username=alice`        – Xóa session\n\n"
        "---\n\n"
        "### 📡 Realtime Socket Events → App_center\n"
        "Đăng ký và xác thực tự động push sự kiện lên `App_center` qua WebSocket.\n\n"
        "> **Hub endpoint:** `ws://localhost:8000/ws/producer`  \n"
        "> **type:** `face_recognition` → **topic:** `security` | **priority:** `high`\n\n"
        "| Sự kiện | `event` | type | Khi nào bắn | Socket? |\n"
        "|---------|---------|------|-------------|--------|\n"
        "| Chụp 1 góc NFC+Face | `enroll_nfc_angle` | `nfc_enroll` | Mỗi khi chụp thành công 1 góc | ✅ |\n"
        "| Hoàn thành NFC+Face | `enroll_nfc_done` | `nfc_enroll` | Sau khi finish | ✅ |\n"
        "| Chụp 1 góc đăng ký | `enroll3_angle` | `face_recognition` | Mỗi khi chụp thành công 1 góc | ✅ |\n"
        "| Hoàn thành đăng ký | `enroll3_done` | `face_recognition` | Sau khi lưu embedding 3 góc vào DB | ✅ |\n"
        "| Xác thực khớp | `verify_matched` | `face_recognition` | Có mặt + score ≥ 0.6 | ✅ |\n"
        "| Xác thực không khớp | `verify_unmatched` | `face_recognition` | Có mặt + score < 0.6 | ✅ |\n"
        "| Không có mặt | `verify_no_face` | `face_recognition` | Không phát hiện khuôn mặt trong frame | ❌ |\n"
        "| Xác thực thẻ thành công | `verify_card_matched` | `card_verify` | Thẻ hợp lệ + chưa hết hạn | ✅ |\n"
        "| Xác thực thẻ thất bại | `verify_card_failed` | `card_verify` | Thẻ hết hạn (`expired`) hoặc thẻ lạ (`card_not_found`) | ✅ |\n"
        "| Thẻ đã có chủ khác | `enroll_card_duplicate` | `card_verify` | Khi đăng ký thẻ đã thuộc user khác | ✅ |\n\n"
        "---\n\n"
        "### 🎯 Đăng ký khuôn mặt tự động – SSE (3 góc, chỉ mặt)\n"
        "- `GET /enroll3?username=alice&source=0[&position=NhanVien&expiry_date=2027-12-31T00:00:00]`\n\n"
        "> Mỗi góc chụp xong: trả SSE về client **đồng thời** push socket lên App_center.  \n"
        "> SSE event có `frame_b64` (preview full frame) + `face_crop_b64` (ảnh mặt crop).\n\n"
        "---\n\n"
        "### 🔍 Xác thực khuôn mặt liên tục – SSE (1s/lần)\n"
        "- `GET /verify3?source=0[&username=alice]`\n\n"
        "> Mỗi 1 giây: chụp frame → detect → so khớp DB → bắn SSE + push socket.\n\n"
        "---\n\n"
        "### 🎯 Đăng ký khuôn mặt thủ công (3 góc)\n"
        "1. `POST /enroll/init-user?username=alice[&position=NhanVien&expiry_date=2027-12-31T00:00:00]`\n"
        "2. `POST /enroll/capture/thang?username=alice&source=0`\n"
        "3. `POST /enroll/capture/trai?username=alice&source=0`\n"
        "4. `POST /enroll/capture/phai?username=alice&source=0`\n"
        "5. `POST /enroll/save?username=alice`\n\n"
        "---\n\n"
        "### 👥 Quản lý người dùng\n"
        "- `GET /users`                                                                   – Danh sách users (trả về `expiry_date`, `card_id`)\n"
        "- `PATCH /users/{username}[?new_username=x&position=y&expiry_date=2027-12-31]`  – Đổi tên / chức vụ / ngày hết hạn / ảnh\n"
        "- `DELETE /users/{username}`                                                     – Xóa 1 user\n"
        "- `DELETE /users`                                                                – Xóa tất cả\n\n"
        "> **`expiry_date`**: ISO 8601 string, VD `2027-12-31` hoặc `2027-12-31T00:00:00`.  \n"
        "> **`card_id`**: NFC/RFID UID dạng hex in hoa (VD: `A66AB0AA`). Gán qua `/enroll/nfc/card` hoặc `/enroll/nfc/finish`.\n\n"
        "### 📋 Tiện ích đăng ký thủ công\n"
        "- `GET /enroll/status?username=alice`   – Xem góc đã chụp\n"
        "- `DELETE /enroll/reset?username=alice` – Xóa session\n"
    ),
    version="3.0.0",
    lifespan=lifespan,
)

# CORS — cho phép mọi origin (frontend, tablet, mobile, v.v.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth_router.router, tags=["Face"])
app.include_router(stream_router.router, tags=["Stream"])
app.include_router(enroll3_router.router, tags=["Enroll 3 Angles"])
app.include_router(verify3_router.router, tags=["Verify 3 Angles"])
app.include_router(enroll_manual_router.router, prefix="/enroll", tags=["Enroll Manual"])
app.include_router(enroll_nfc_router.router, tags=["Enroll NFC + Face"])
app.include_router(verify_card_router.router, tags=["Verify Card"])
app.include_router(enroll_finger_router.router, tags=["Enroll Fingerprint"])
app.include_router(verify_finger_router.router, tags=["Verify Finger"])


@app.get("/", tags=["UI"])
def index():
    """Serve giao diện web HTML/CSS/JS"""
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


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
