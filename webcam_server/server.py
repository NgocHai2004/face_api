"""
webcam_server — Standalone MJPEG stream server
Đọc camera 1 lần, chia sẻ realtime cho nhiều client (browser, VLC, project khác).

Endpoints:
  GET /           → trang HTML xem video
  GET /stream     → MJPEG stream  (dùng trong <img src="/stream">)
  GET /snapshot   → 1 frame JPEG
  GET /health     → status JSON
  GET /clients    → số client đang xem

Dùng:
  python server.py
  # hoặc
  uvicorn server:app --host 0.0.0.0 --port 8090

Camera source:
  CAMERA_SOURCE=pi   → dùng rpicam-vid (Raspberry Pi camera module)
  CAMERA_SOURCE=0    → /dev/video0 qua V4L2
  CAMERA_SOURCE=rtsp://...  → RTSP stream qua FFmpeg
"""
import asyncio
import subprocess
import cv2
import threading
import time
import socket
from typing import Optional
import numpy as np

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from contextlib import asynccontextmanager


# ─────────────────────────────────────────────────────────────────
# Config (có thể override qua .env)
# ─────────────────────────────────────────────────────────────────
import os
from dotenv import load_dotenv
load_dotenv()

CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "pi")   # "pi" | "0" | "rtsp://..."
JPEG_QUALITY  = int(os.getenv("JPEG_QUALITY", "70"))
TARGET_FPS    = int(os.getenv("TARGET_FPS", "20"))
RESIZE_WIDTH  = int(os.getenv("RESIZE_WIDTH", "640"))
HOST          = os.getenv("HOST", "0.0.0.0")
PORT          = int(os.getenv("PORT", "8090"))

# Raspberry Pi camera settings (chỉ dùng khi CAMERA_SOURCE == "pi")
PI_WIDTH      = int(os.getenv("PI_WIDTH", "640"))
PI_HEIGHT     = int(os.getenv("PI_HEIGHT", "480"))
PI_FRAMERATE  = int(os.getenv("PI_FRAMERATE", str(TARGET_FPS)))


# ─────────────────────────────────────────────────────────────────
# Frame broadcaster — đọc camera 1 lần, push cho nhiều subscriber
# ─────────────────────────────────────────────────────────────────
class FrameBroadcaster:
    def __init__(self):
        self._jpeg: Optional[bytes] = None
        self._lock  = threading.Lock()
        self._event = asyncio.Event()          # notifies async consumers
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._client_count = 0
        self._fps_actual = 0.0
        self._source_label = ""

    def start(self, source_str: str):
        self._source_label = source_str
        self._running = True
        if source_str.strip().lower() == "pi":
            self._thread = threading.Thread(
                target=self._capture_loop_pi, daemon=True)
        else:
            src = self._parse(source_str)
            self._thread = threading.Thread(
                target=self._capture_loop_cv2, args=(src,), daemon=True)
        self._thread.start()

    def _parse(self, s: str):
        """
        "0" hoặc "1" → "/dev/video0" (V4L2 device path)
        RTSP/HTTP URL → giữ nguyên string
        "/dev/videoX" → giữ nguyên
        """
        s = s.strip()
        if s.lstrip("-").isdigit():
            return f"/dev/video{s}"
        return s

    # ------------------------------------------------------------------
    # Capture loop — Raspberry Pi camera via rpicam-vid (YUV420)
    # ------------------------------------------------------------------
    def _capture_loop_pi(self):
        frame_size = PI_WIDTH * PI_HEIGHT * 3 // 2  # YUV420

        cmd = [
            "rpicam-vid",
            "-t", "0",
            "--width",     str(PI_WIDTH),
            "--height",    str(PI_HEIGHT),
            "--framerate", str(PI_FRAMERATE),
            "--codec",     "yuv420",
            "-n",
            "-o", "-",
        ]
        print(f"[webcam_server] Starting Pi camera: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        fps_counter, fps_t0 = 0, time.monotonic()

        try:
            while self._running:
                data = proc.stdout.read(frame_size)
                if len(data) != frame_size:
                    print("[webcam_server] Pi camera: incomplete frame, restarting...")
                    break

                yuv = np.frombuffer(data, dtype=np.uint8).reshape(
                    (PI_HEIGHT * 3 // 2, PI_WIDTH)
                )
                frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

                # Optional: resize
                h, w = frame.shape[:2]
                if w > RESIZE_WIDTH:
                    frame = cv2.resize(
                        frame, (RESIZE_WIDTH, int(h * RESIZE_WIDTH / w))
                    )

                _, buf = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                )
                jpeg = buf.tobytes()

                with self._lock:
                    self._jpeg = jpeg

                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._event.set)

                # FPS counter
                fps_counter += 1
                elapsed_fps = time.monotonic() - fps_t0
                if elapsed_fps >= 2.0:
                    self._fps_actual = fps_counter / elapsed_fps
                    fps_counter, fps_t0 = 0, time.monotonic()

        finally:
            proc.terminate()
            print("[webcam_server] Pi capture loop stopped")

    # ------------------------------------------------------------------
    # Capture loop — V4L2 / RTSP via OpenCV
    # ------------------------------------------------------------------
    def _capture_loop_cv2(self, src):
        if src.startswith("/dev/video"):
            cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        else:
            cap = cv2.VideoCapture(src)

        if not cap.isOpened():
            print(f"[webcam_server] ERROR: Cannot open source: {src}")
            return

        print(f"[webcam_server] Opened source: {src}")
        interval = 1.0 / TARGET_FPS
        fps_counter, fps_t0 = 0, time.monotonic()

        while self._running:
            t0 = time.monotonic()
            ret, frame = cap.read()
            if not ret or frame is None:
                cap.release()
                time.sleep(0.5)
                if src.startswith("/dev/video"):
                    cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
                else:
                    cap = cv2.VideoCapture(src)
                continue

            # Resize
            h, w = frame.shape[:2]
            if w > RESIZE_WIDTH:
                frame = cv2.resize(frame, (RESIZE_WIDTH, int(h * RESIZE_WIDTH / w)))

            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            jpeg = buf.tobytes()

            with self._lock:
                self._jpeg = jpeg

            # Notify async consumers
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._event.set)

            # FPS counter
            fps_counter += 1
            elapsed_fps = time.monotonic() - fps_t0
            if elapsed_fps >= 2.0:
                self._fps_actual = fps_counter / elapsed_fps
                fps_counter, fps_t0 = 0, time.monotonic()

            # Throttle
            elapsed = time.monotonic() - t0
            sleep_t = interval - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

        cap.release()
        print("[webcam_server] Capture loop stopped")

    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg

    def register_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def wait_new_frame(self):
        self._event.clear()
        await self._event.wait()

    def stop(self):
        self._running = False


broadcaster = FrameBroadcaster()


# ─────────────────────────────────────────────────────────────────
# App lifecycle
# ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    broadcaster.register_loop(loop)
    broadcaster.start(CAMERA_SOURCE)
    local_ip = _get_local_ip()
    print(f"[webcam_server] Serving on http://{local_ip}:{PORT}")
    yield
    broadcaster.stop()


app = FastAPI(
    title="Webcam Server",
    description="Standalone MJPEG stream server — share camera realtime cho nhiều client",
    version="2.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────
# MJPEG stream
# ─────────────────────────────────────────────────────────────────
async def _mjpeg_generator():
    broadcaster._client_count += 1
    try:
        while True:
            await broadcaster.wait_new_frame()
            jpeg = broadcaster.get_jpeg()
            if jpeg is None:
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg +
                b"\r\n"
            )
    except asyncio.CancelledError:
        pass
    finally:
        broadcaster._client_count -= 1


@app.get("/stream", summary="MJPEG stream — dùng trong <img src='/stream'>")
async def stream():
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ─────────────────────────────────────────────────────────────────
# Snapshot
# ─────────────────────────────────────────────────────────────────
@app.get("/snapshot", summary="Lấy 1 frame JPEG")
async def snapshot():
    jpeg = broadcaster.get_jpeg()
    if jpeg is None:
        return Response(status_code=503, content="No frame available")
    return Response(content=jpeg, media_type="image/jpeg")


# ─────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────
@app.get("/health", summary="Health check")
def health():
    return {
        "status": "ok",
        "source": broadcaster._source_label,
        "fps": round(broadcaster._fps_actual, 1),
        "clients": broadcaster._client_count,
        "target_fps": TARGET_FPS,
        "jpeg_quality": JPEG_QUALITY,
        "resize_width": RESIZE_WIDTH,
    }


@app.get("/clients", summary="Số client đang xem")
def clients():
    return {"clients": broadcaster._client_count}


# ─────────────────────────────────────────────────────────────────
# HTML viewer
# ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, summary="Xem video trong browser")
def index():
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <title>Webcam Server</title>
  <style>
    body {{ margin:0; background:#111; display:flex; flex-direction:column;
            align-items:center; justify-content:center; min-height:100vh; color:#eee; font-family:sans-serif; }}
    h2   {{ margin-bottom:8px; }}
    img  {{ max-width:100%; border:2px solid #333; border-radius:8px; }}
    #info{{ margin-top:10px; font-size:.85rem; color:#aaa; }}
  </style>
</head>
<body>
  <h2>📷 Webcam Server</h2>
  <img src="/stream" alt="camera stream">
  <div id="info">
    Source: <b>{CAMERA_SOURCE}</b> &nbsp;|&nbsp; Target: <b>{TARGET_FPS} fps</b>
    &nbsp;|&nbsp; MJPEG URL: <code>http://{{location.hostname}}:{PORT}/stream</code>
  </div>
  <script>
    document.getElementById('info').innerHTML =
      document.getElementById('info').innerHTML.replace(
        '{{location.hostname}}', location.hostname);
  </script>
</body>
</html>""")


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────
def _get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ─────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
