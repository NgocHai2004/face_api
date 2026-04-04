import cv2
import numpy as np
from typing import Optional
from app.config import settings
from app.camera_manager import camera_manager


def _parse_source(source):
    """
    Quy tắc chuyển đổi source:
      - "0" / 0         → RTSP1 từ .env nếu có (webcam_server stream),
                          ngược lại int 0 (mở camera trực tiếp)
      - "1" / 1         → RTSP1 từ .env (nếu có), ngược lại int 1
      - "2" / 2         → RTSP2 từ .env (nếu có), ngược lại int 2
      - "http://..."    → giữ nguyên (MJPEG URL từ webcam_server)
      - "rtsp://..."    → giữ nguyên RTSP URL
    """
    # Nếu đã là URL (http/https/rtsp) → giữ nguyên
    if isinstance(source, str):
        lower = source.lower()
        if lower.startswith("rtsp") or lower.startswith("http"):
            return source

    try:
        idx = int(source)
    except (ValueError, TypeError):
        return source  # chuỗi bất kỳ khác, trả về nguyên

    # index 0 → ưu tiên dùng RTSP1 từ .env (webcam_server stream)
    if idx == 0:
        rtsp1 = getattr(settings, "RTSP1", None)
        if rtsp1 and rtsp1.strip():
            return rtsp1.strip()
        return 0  # không có RTSP1 → mở camera trực tiếp

    # index ≥ 1 → thử lấy RTSP{idx} từ .env
    rtsp_url = getattr(settings, f"RTSP{idx}", None)
    if rtsp_url and rtsp_url.strip():
        return rtsp_url.strip()

    # Không có RTSP{idx} trong .env → fallback về RTSP1, rồi int 0
    rtsp1 = getattr(settings, "RTSP1", None)
    if rtsp1 and rtsp1.strip():
        print(f"[SOURCE] RTSP{idx} not found, falling back to RTSP1")
        return rtsp1.strip()
    print(f"[SOURCE] RTSP{idx} not found in .env, falling back to camera index 0")
    return 0


def capture_frame_from_rtsp(source, max_retries: int = 3) -> Optional[np.ndarray]:
    """
    Capture one frame from an RTSP URL or local camera index.
    - source = "0" / 0   → dùng CameraManager (singleton, tránh lock camera)
    - source = "rtsp://..." → mở/đọc/đóng trực tiếp
    """
    src = _parse_source(source)

    # ── Local camera: dùng singleton CameraManager ────────────
    if isinstance(src, int):
        if not camera_manager.is_running or camera_manager._source != src:
            ok = camera_manager.start(src)
            if not ok:
                print(f"[CAM] Cannot open camera index {src}")
                return None
        # Chờ frame đầu tiên tối đa 3s
        import time
        for _ in range(30):
            frame = camera_manager.get_frame()
            if frame is not None:
                return frame
            time.sleep(0.1)
        print(f"[CAM] Timeout waiting for frame from camera {src}")
        return None

    # ── HTTP(S) MJPEG/snapshot URL ─────────────────────────────
    if isinstance(src, str) and src.lower().startswith("http"):
        # Chuyển MJPEG stream URL → snapshot URL nếu webcam_server
        # http://host:port/stream → http://host:port/snapshot
        snapshot_url = src.replace("/stream", "/snapshot")
        frame = fetch_snapshot_from_url(snapshot_url)
        if frame is not None:
            return frame
        # fallback: thử đọc trực tiếp qua cv2
        for attempt in range(1, max_retries + 1):
            cap = cv2.VideoCapture(src)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    return frame
            cap.release()
        return None

    # ── RTSP: open/read/close ──────────────────────────────────
    for attempt in range(1, max_retries + 1):
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            print(f"[RTSP] Attempt {attempt}: Cannot open {src}")
            cap.release()
            continue
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            return frame
        print(f"[RTSP] Attempt {attempt}: Failed to read frame")
    return None


def fetch_snapshot_from_url(url: str, timeout: float = 2.0) -> Optional[np.ndarray]:
    """
    Lấy 1 frame JPEG từ HTTP snapshot endpoint (ví dụ webcam_server /snapshot).
    Nhanh hơn nhiều so với mở cv2.VideoCapture MJPEG stream.
    """
    try:
        import urllib.request
        print(f"[CAM] Fetching snapshot from {url}", flush=True)
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
        arr = np.frombuffer(data, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is not None:
            print(f"[CAM] Snapshot OK — shape={frame.shape}", flush=True)
        else:
            print(f"[CAM] Snapshot decode failed (empty frame)", flush=True)
        return frame if frame is not None else None
    except Exception as e:
        print(f"[SNAPSHOT] Failed to fetch {url}: {e}", flush=True)
        return None


def capture_frame_from_any_stream() -> Optional[tuple[str, np.ndarray]]:
    """Try each RTSP stream in .env, return (url, frame) for first success."""
    for url in settings.get_rtsp_streams():
        frame = capture_frame_from_rtsp(url)
        if frame is not None:
            return url, frame
    return None
