# webcam_server

Standalone MJPEG stream server — đọc camera **1 lần**, share realtime cho **nhiều client** (browser, VLC, project khác).

Hỗ trợ:
- **Raspberry Pi camera module** (`rpicam-vid` → YUV420) ← mặc định
- **USB webcam / V4L2** (`/dev/video0`, ...)
- **RTSP / HTTP stream** (IP cam, ...)

## Cấu trúc
```
webcam_server/
├── server.py        ← FastAPI app
├── .env             ← cấu hình (tùy chọn)
├── requirements.txt
└── venv/            ← Python virtual environment
```

## Cài đặt

```bash
cd webcam_server
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

> ⚠️ Trên Raspberry Pi OS (Bookworm+), dùng `venv/bin/pip` thay vì `pip` để tránh lỗi externally-managed-environment.

## Chạy

```bash
# Dùng venv trực tiếp (khuyến nghị trên Pi)
venv/bin/python server.py

# Hoặc activate venv trước
source venv/bin/activate
python server.py

# Hoặc uvicorn
venv/bin/uvicorn server:app --host 0.0.0.0 --port 8090
```

## Endpoints

| URL | Mô tả |
|-----|--------|
| `http://pi:8090/` | Xem video trong browser |
| `http://pi:8090/stream` | MJPEG stream — dùng trong `<img src="...">` |
| `http://pi:8090/snapshot` | Lấy 1 frame JPEG |
| `http://pi:8090/health` | FPS thực tế, số client, cấu hình |
| `http://pi:8090/clients` | Số client đang xem |

## Cấu hình `.env`

```env
# Nguồn camera:
#   pi          → Raspberry Pi camera module (rpicam-vid, mặc định)
#   0           → /dev/video0 qua V4L2
#   rtsp://...  → RTSP stream
CAMERA_SOURCE=pi

# Pi camera settings (chỉ dùng khi CAMERA_SOURCE=pi)
PI_WIDTH=640
PI_HEIGHT=480
PI_FRAMERATE=20

# Chung
JPEG_QUALITY=70          # 1-100
TARGET_FPS=20
RESIZE_WIDTH=640         # resize output (0 = không resize)
PORT=8090
HOST=0.0.0.0

# Face detection bbox (1=bật, 0=tắt)
FACE_DETECTION=1
```

## Dùng USB webcam

```env
CAMERA_SOURCE=0
```

## Dùng RTSP làm nguồn

```env
CAMERA_SOURCE=rtsp://admin:password@192.168.1.100:554/stream
```

## Tích hợp với Recognition_api

Sau khi `webcam_server` chạy, sửa `Recognition_api/.env`:
```env
RTSP1=http://localhost:8090/stream
```

## Chạy song song cả 2 project

```bash
# Terminal 1 — webcam server (port 8090)
cd webcam_server && venv/bin/python server.py

# Terminal 2 — recognition API (port 8001)
cd Recognition_api && python run.py
```
