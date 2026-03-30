"""
Event Hub - FastAPI Application Entry Point

Khởi động: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.event_bus import event_bus
from core.image_store import FACE_STORAGE_PATH
from api import ws_producer_router, ws_consumer_router, rest_events_router

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------
load_dotenv()

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("event_hub")


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("  Event Hub starting up...")
    logger.info("=" * 60)
    await event_bus.start()
    logger.info("EventBus dispatcher started ✓")
    yield
    logger.info("Event Hub shutting down...")
    await event_bus.stop()
    logger.info("EventBus stopped ✓")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
# Lấy IP thực của máy để hiển thị trong Swagger
import socket as _socket
def _get_local_ip() -> str:
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"

_LOCAL_IP = _get_local_ip()

app = FastAPI(
    title       = "Event Hub API",
    description = """
## 🔌 Event Hub — Realtime Event Middleware

Trung gian chuẩn hóa sự kiện realtime. **Producers** gửi raw events → Hub normalize → **Consumers** nhận events chuẩn.

---

## 📡 WebSocket Endpoints

### Producer (gửi event)
```
ws://192.168.23.46:8000/ws/producer
ws://192.168.23.46:8000/ws/producer?source=camera_01
```
Gửi JSON — Face Recognition:
```json
{
  "source": "face_recognition_api",
  "type": "face_recognition",
  "priority": "high",
  "payload": {
    "username":         "Nguyen Van A",
    "matched":          true,
    "similarity":       0.97,
    "face_crop_base64": "<base64_string>",
    "timestamp":        "2026-03-29T10:54:17.445206",
    "rtsp_url":         "rtsp://camera_ip/stream",
    "message":          "Nhận diện thành công"
  }
}
```
Gửi JSON — Fingerprint:
```json
{
  "source": "fingerprint_reader_01",
  "type": "fingerprint",
  "priority": "high",
  "payload": {
    "person_id":   "EMP001",
    "person_name": "Nguyen Van A",
    "finger_id":   3,
    "confidence":  0.99,
    "action":      "entry",
    "location":    "main_entrance",
    "reader_id":   "FP-001"
  }
}
```
Nhận ACK:
```json
{ "status": "ok", "event_id": "uuid", "topic": "security", "queued": true }
```

### Consumer (nhận event realtime)
```
ws://<IP>:8000/ws/consumer?topic=*
ws://<IP>:8000/ws/consumer?topic=security
```
Nhận NormalizedEvent:
```json
{
  "id": "uuid-v4",
  "timestamp": "2026-01-01T00:00:00Z",
  "source": "camera_01",
  "type": "face_recognition",
  "topic": "security",
  "priority": "high",
  "payload": { "person_id": "EMP001" },
  "metadata": { "normalized": true, "version": "1.0" }
}
```
> **Lưu ý:** Message đầu tiên khi kết nối là `__system__` message, bỏ qua khi `type == "__system__"`.

---

## 🗺️ Type → Topic Mapping

| type | topic |
|------|-------|
| `face_recognition` | `security` |
| `fingerprint` | `security` |

---

## 🔑 Priority Values
`low` · `medium` *(default)* · `high` · `urgent`
""",
    version     = "1.0.0",
    lifespan    = lifespan,
    contact     = {
        "name": "Event Hub",
        "url":  f"http://{_LOCAL_IP}:5173",
    },
    license_info = {
        "name": "MIT",
    },
    servers = [
        {"url": f"http://{_LOCAL_IP}:8000", "description": f"LAN — {_LOCAL_IP}"},
        {"url": "http://localhost:8000",     "description": "Local"},
    ],
    openapi_tags = [
        {
            "name": "Producer",
            "description": "Gửi events vào Hub (REST thay thế cho WebSocket Producer)",
        },
        {
            "name": "Consumer",
            "description": "Query lịch sử events (REST thay thế cho WebSocket Consumer)",
        },
        {
            "name": "System",
            "description": "Health check, event types, thông tin hệ thống",
        },
    ],
)

# ---------------------------------------------------------------------------
# CORS - cho phép React frontend truy cập
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins     = CORS_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(ws_producer_router)
app.include_router(ws_consumer_router)
app.include_router(rest_events_router)

# ---------------------------------------------------------------------------
# Static files - serve ảnh face crop từ /mnt/faces/
# ---------------------------------------------------------------------------
FACE_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
app.mount("/faces", StaticFiles(directory=str(FACE_STORAGE_PATH)), name="faces")
logger.info("Face images served at /faces → %s", FACE_STORAGE_PATH)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------
@app.get("/", tags=["System"])
async def root():
    return {
        "name"       : "Event Hub",
        "version"    : "1.0.0",
        "status"     : "running",
        "endpoints"  : {
            "ws_producer"  : f"ws://{_LOCAL_IP}:8000/ws/producer",
            "ws_consumer"  : f"ws://{_LOCAL_IP}:8000/ws/consumer?topic=*",
            "ingest"       : "POST /events/ingest",
            "recent"       : "GET  /events/recent?topic=&limit=",
            "topics"       : "GET  /events/topics",
            "event_types"  : "GET  /events/types",
            "health"       : "GET  /health",
            "faces"        : f"GET  http://{_LOCAL_IP}:8000/faces/<filename>",
            "docs"         : "GET  /docs",
        },
    }
