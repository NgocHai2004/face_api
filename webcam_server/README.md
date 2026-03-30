# webcam_server

Standalone MJPEG stream server — đọc camera **1 lần**, share realtime cho **nhiều client** (browser, VLC, project khác).

## Cấu trúc
```
webcam_server/
├── server.py        ← FastAPI app
├── .env             ← cấu hình
└── requirements.txt
```

## Cài đặt
```bash
cd webcam_server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Chạy
```bash
python server.py
# hoặc
uvicorn server:app --host 0.0.0.0 --port 8090
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
CAMERA_SOURCE=0          # USB cam index, hoặc rtsp://...
JPEG_QUALITY=70          # 1-100
TARGET_FPS=20
RESIZE_WIDTH=640         # resize output
PORT=8090
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
hoặc dùng RTSP re-stream qua mediamtx.

## Chạy song song cả 2 project
```bash
# Terminal 1:
cd webcam_server && python server.py    # port 8090

# Terminal 2:
cd Recognition_api && python run.py    # port 8001
```
