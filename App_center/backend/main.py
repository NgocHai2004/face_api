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

    # Khởi tạo MongoDB (Beanie)
    try:
        from core.database import init_db
        await init_db()
        logger.info("MongoDB (Beanie) initialized ✓")
    except Exception as exc:
        logger.warning("MongoDB unavailable, running without persistence: %s", exc)

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
ws://localhost:8000/ws/producer
ws://localhost:8000/ws/producer?source=camera_01
```
Nhận ACK:
```json
{ "status": "ok", "event_id": "uuid", "topic": "security", "queued": true }
```

### Consumer (nhận event realtime)
```
ws://<IP>:8000/ws/consumer?topic=*         ← nhận tất cả events
ws://<IP>:8000/ws/consumer?topic=security  ← chỉ nhận security events
```

**Message hệ thống khi kết nối** (bỏ qua nếu `type == "__system__"`):
```json
{ "type": "__system__", "status": "connected", "subscribed_topic": "security" }
```

**NormalizedEvent nhận được:**
```json
{
  "id":        "uuid-v4",
  "timestamp": "2026-04-03T10:00:00Z",
  "source":    "face_recognition_api",
  "type":      "face_recognition | nfc_enroll | card_verify",
  "topic":     "security",
  "priority":  "high",
  "payload":   { "event": "verify_matched | enroll_nfc_done | ...", "..." : "..." },
  "metadata":  { "normalized": true, "version": "1.0", "received_at": "..." }
}
```

**Tất cả `payload.event` consumer có thể nhận:**

| `payload.event` | `type` | `topic` | Ý nghĩa |
|---|---|---|---|
| `verify_matched` | `face_recognition` | `security` | Xác thực mặt thành công |
| `verify_unmatched` | `face_recognition` | `security` | Mặt không khớp DB |
| `enroll3_angle` | `face_recognition` | `security` | Chụp 1 góc (chỉ mặt) |
| `enroll3_done` | `face_recognition` | `security` | Xong đăng ký 3 góc mặt |
| `enroll_nfc_angle` | `nfc_enroll` | `security` | Chụp 1 góc (NFC+Face) |
| `enroll_nfc_done` | `nfc_enroll` | `security` | Xong đăng ký NFC+Face |
| `verify_card_matched` | `card_verify` | `security` | Xác thực thẻ thành công |
| `verify_card_failed` | `card_verify` | `security` | Thẻ hết hạn / thẻ lạ |
| `enroll_card_duplicate` | `card_verify` | `security` | Thẻ đã thuộc người khác |

**Đổi topic sau khi kết nối:**
```json
{ "action": "change_topic", "topic": "security" }
```

---

## 🤖 Events từ Recognition_api (`:8001`)

> Hub tự động intercept `face_crop_b64` trong payload → lưu file `/mnt/faces/` → thay bằng `face_image_url` (HTTP URL) và `face_image_path`.

### Xác thực khuôn mặt (`/verify3`) — mỗi 1 giây bắn 1 event

**Xác thực thành công** `payload.event = "verify_matched"` — `payload.matched = true`:
```json
{
  "source": "face_recognition_api", "type": "face_recognition", "priority": "high",
  "payload": {
    "event":           "verify_matched",
    "phase":           "matched",
    "matched":         true,
    "username":        "nguyen_van_a",
    "position":        "Nhan vien",
    "score":           0.8542,
    "source":          "http://192.168.x.x:8090/stream",
    "timestamp":       "2026-04-02T10:00:00",
    "face_image_url":  "http://192.168.x.x:8000/faces/face_xxx.jpg",
    "face_image_path": "/mnt/faces/face_xxx.jpg",
    "message":         "✅ Xác thực thành công: nguyen_van_a (0.8542)"
  }
}
```

**Có mặt nhưng không khớp** `payload.event = "verify_unmatched"` — `payload.matched = false`:
```json
{
  "source": "face_recognition_api", "type": "face_recognition", "priority": "high",
  "payload": {
    "event":            "verify_unmatched",
    "phase":            "scanning",
    "matched":          false,
    "username":         null,
    "nearest":          "nguyen_van_a",
    "nearest_position": "Nhan vien",
    "score":            0.3210,
    "source":           "http://192.168.x.x:8090/stream",
    "timestamp":        "2026-04-02T10:00:01",
    "face_image_url":   "http://192.168.x.x:8000/faces/face_xxx.jpg",
    "face_image_path":  "/mnt/faces/face_xxx.jpg",
    "message":          "❌ Không nhận diện được — gần nhất: nguyen_van_a (score=0.321)"
  }
}
```

> **`verify_no_face`** (không có mặt trong frame) **không** push lên Hub — chỉ trả SSE về client.

---

### Đăng ký khuôn mặt (`/enroll3`)

**Mỗi góc chụp thành công** → `payload.event = "enroll3_angle"`:
```json
{
  "source": "face_recognition_api", "type": "face_recognition", "priority": "high",
  "payload": {
    "event":           "enroll3_angle",
    "step":            1,
    "total_steps":     3,
    "required_angle":  "THANG",
    "face_direction":  "THANG",
    "captured":        "THANG",
    "username":        "nguyen_van_a",
    "source":          "0",
    "timestamp":       "2026-04-02T10:00:00",
    "face_image_url":  "http://192.168.x.x:8000/faces/face_xxx.jpg",
    "face_image_path": "/mnt/faces/face_xxx.jpg",
    "message":         "✅ Đã chụp góc THANG cho 'nguyen_van_a'!"
  }
}
```

**Hoàn thành đăng ký 3 góc** → `payload.event = "enroll3_done"`:
```json
{
  "source": "face_recognition_api", "type": "face_recognition", "priority": "high",
  "payload": {
    "event":           "enroll3_done",
    "done":            true,
    "username":        "nguyen_van_a",
    "angles_captured": ["THANG", "TRAI", "PHAI"],
    "source":          "0",
    "timestamp":       "2026-04-02T10:00:05",
    "message":         "✅ Đăng ký thành công 3 góc cho 'nguyen_van_a'!"
  }
}
```

---

### Đăng ký NFC + Khuôn mặt (`/enroll/nfc/stream` + `/enroll/nfc/finish`)

**Mỗi góc chụp thành công** → `payload.event = "enroll_nfc_angle"` — `type = "nfc_enroll"`:
```json
{
  "source": "face_recognition_api", "type": "nfc_enroll", "priority": "high",
  "payload": {
    "event":          "enroll_nfc_angle",
    "step":           1,
    "total_steps":    3,
    "required_angle": "THANG",
    "captured":       "THANG",
    "username":       "nguyen_van_a",
    "position":       "NhanVien",
    "expiry_date":    "2027-12-31T00:00:00",
    "source":         "0",
    "timestamp":      "2026-04-03T10:00:00",
    "face_image_url": "http://192.168.x.x:8000/faces/face_xxx.jpg",
    "message":        "✅ Đã chụp góc THANG cho 'nguyen_van_a'!"
  }
}
```

**Hoàn thành đăng ký NFC + Face** → `payload.event = "enroll_nfc_done"` — `type = "nfc_enroll"`:
```json
{
  "source": "face_recognition_api", "type": "nfc_enroll", "priority": "high",
  "payload": {
    "event":           "enroll_nfc_done",
    "done":            true,
    "username":        "nguyen_van_a",
    "position":        "NhanVien",
    "expiry_date":     "2027-12-31T00:00:00",
    "face_ok":         true,
    "card_ok":         true,
    "angles_captured": ["THANG", "TRAI", "PHAI"],
    "card_id":         "A66AB0AA",
    "registered_with": "khuôn mặt 3 góc + thẻ NFC (A66AB0AA)",
    "timestamp":       "2026-04-03T10:00:10",
    "message":         "✅ Đăng ký thành công cho 'nguyen_van_a'!"
  }
}
```

---

### Xác thực thẻ NFC (`/verify/card`)

**Xác thực thành công** → `payload.event = "verify_card_matched"` — `type = "card_verify"`:
```json
{
  "source": "face_recognition_api", "type": "card_verify", "priority": "high",
  "payload": {
    "event":       "verify_card_matched",
    "card_id":     "A66AB0AA",
    "username":    "nguyen_van_a",
    "position":    "NhanVien",
    "expiry_date": "2027-12-31T00:00:00",
    "matched":     true,
    "reason":      "ok",
    "timestamp":   "2026-04-03T10:00:00",
    "message":     "✅ Xác thực thẻ thành công: nguyen_van_a"
  }
}
```

**Thẻ hết hạn** → `payload.event = "verify_card_failed"` / `reason = "expired"`:
```json
{
  "source": "face_recognition_api", "type": "card_verify", "priority": "high",
  "payload": {
    "event":       "verify_card_failed",
    "card_id":     "A66AB0AA",
    "username":    "nguyen_van_a",
    "matched":     false,
    "reason":      "expired",
    "timestamp":   "2026-04-03T10:00:00",
    "message":     "❌ Thẻ A66AB0AA (nguyen_van_a) đã hết hạn"
  }
}
```

**Thẻ lạ (chưa đăng ký)** → `payload.event = "verify_card_failed"` / `reason = "card_not_found"`:
```json
{
  "source": "face_recognition_api", "type": "card_verify", "priority": "high",
  "payload": {
    "event":    "verify_card_failed",
    "card_id":  "863842AB",
    "username": null,
    "matched":  false,
    "reason":   "card_not_found",
    "message":  "❌ Thẻ 863842AB không tìm thấy trong hệ thống"
  }
}
```

**Thẻ đã thuộc người khác (khi đăng ký)** → `payload.event = "enroll_card_duplicate"` — `type = "card_verify"`:
```json
{
  "source": "face_recognition_api", "type": "card_verify", "priority": "high",
  "payload": {
    "event":                   "enroll_card_duplicate",
    "card_id":                 "863842AB",
    "requested_by":            "alice",
    "current_owner":           "bob",
    "current_owner_position":  "QuanLy",
    "current_owner_expiry":    "2027-12-31T00:00:00",
    "matched":                 false,
    "reason":                  "card_already_registered",
    "message":                 "❌ Thẻ 863842AB đã được đăng ký cho 'bob' (QuanLy)"
  }
}
```

---

## 🗺️ Type → Topic Mapping

| type | topic |
|------|-------|
| `face_recognition` | `security` |
| `nfc_enroll` | `security` |
| `card_verify` | `security` |
| `fingerprint` | `security` |
| `card_reader` | `security` |
| `custom` | `custom` |

---

## 🔑 Priority Values
`low` · `medium` *(default)* · `high` · `urgent`

---

## 📋 Phân biệt loại event qua `payload.event`

| `payload.event` | Nguồn | Ý nghĩa | Socket? |
|----------------|-------|---------|---------|
| `verify_matched` | `/verify3` | Có mặt + score ≥ 0.6 → khớp | ✅ |
| `verify_unmatched` | `/verify3` | Có mặt + score < 0.6 → không khớp | ✅ |
| `enroll3_angle` | `/enroll3` | Chụp thành công 1 góc (chỉ mặt) | ✅ |
| `enroll3_done` | `/enroll3` | Hoàn thành đăng ký 3 góc (chỉ mặt) | ✅ |
| `enroll_nfc_angle` | `/enroll/nfc/stream` | Chụp 1 góc (NFC+Face) | ✅ |
| `enroll_nfc_done` | `/enroll/nfc/finish` | Hoàn thành đăng ký NFC+Face | ✅ |
| `verify_card_matched` | `/verify/card` | Xác thực thẻ thành công | ✅ |
| `verify_card_failed` | `/verify/card` | Thẻ hết hạn hoặc thẻ lạ | ✅ |
| `enroll_card_duplicate` | `/enroll/nfc/card` | Thẻ đã thuộc người khác | ✅ |
| `verify_no_face` | `/verify3` | Không phát hiện khuôn mặt | ❌ |

**Phân biệt matched/unmatched nhanh:**
- `payload.matched == true` → người được nhận diện, xem `payload.username` + `payload.position`
- `payload.matched == false` → không nhận ra, xem `payload.nearest` (người gần nhất) + `payload.score`
- `payload.reason` → `"ok"` | `"expired"` | `"card_not_found"` | `"card_already_registered"`
""",
    version     = "1.1.0",
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
