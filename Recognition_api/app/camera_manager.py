"""
CameraManager – singleton quản lý camera.

Pipeline duy nhất (1 background thread):
  1. Đọc frame từ camera (~100fps read loop)
  2. YuNet detect bbox + hướng mặt (~5-10ms, mỗi frame)
  3. InsightFace embed + match (~150-300ms, mỗi EMBED_EVERY frame)
  4. Annotate frame → encode JPEG 1 lần
  5. Cache annotated_jpeg + kết quả nhận diện

Consumer:
  - /webcam MJPEG: lấy annotated_jpeg từ cache (không encode lại)
  - /stream SSE:   lấy annotated_jpeg + recognition_result từ cache
  → Không encode 2 lần, không tranh camera
"""
import threading
import time
import cv2
import numpy as np
from typing import Optional
from datetime import datetime


class CameraManager:
    def __init__(self):
        self._cap: Optional[cv2.VideoCapture] = None
        self._source = None

        # Raw frame (cho enrollment, verify one-shot)
        self._frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()

        # Annotated JPEG bytes (cho /webcam và /stream)
        self._annotated_jpeg: Optional[bytes] = None
        self._jpeg_lock = threading.Lock()

        # Recognition result
        self._recog: dict = {
            "matched": False, "name": None, "score": 0.0,
            "direction": None, "face_count": 0,
            "face_crop": None, "timestamp": None,
        }
        self._recog_lock = threading.Lock()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_read = 0.0

        # Callbacks set by stream router
        self._enable_recognition: bool = False
        self._stored_embeddings: list = []   # [(username, embedding)]
        self._face_threshold: float = 0.5
        self._embed_every: int = 8
        self._embed_counter: int = 0

    # ── Helpers ──────────────────────────────────────────────
    def _open(self, source) -> bool:
        if isinstance(source, int):
            cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap = cv2.VideoCapture(source)
        else:
            cap = cv2.VideoCapture(source)
        if cap.isOpened():
            self._cap = cap
            return True
        return False

    # ── Main capture + process loop ───────────────────────────
    def _capture_loop(self):
        # Lazy imports (not available at module load time on first start)
        from app.fast_detector import get_fast_detector, annotate_faces
        from app.face_utils import extract_embedding_from_image, bytes_to_embedding, verify_faces, crop_to_base64

        detector = get_fast_detector()
        embed_frame_box: list = [None]  # frame waiting for InsightFace
        embed_result_box: list = [{}]   # latest embed result

        embed_running = threading.Event()
        embed_running.set()

        def _embed_worker():
            while embed_running.is_set():
                f = embed_frame_box[0]
                if f is None:
                    time.sleep(0.01)
                    continue
                embed_frame_box[0] = None
                emb, crop = extract_embedding_from_image(f)
                name, score, matched = None, 0.0, False
                embeddings = self._stored_embeddings
                if emb is not None and embeddings:
                    for uname, stored_emb in embeddings:
                        _, s = verify_faces(stored_emb, emb)
                        if s > score:
                            score = s
                            name = uname
                    matched = score >= self._face_threshold
                embed_result_box[0] = {
                    "matched": matched, "name": name, "score": score,
                    "face_crop": crop, "timestamp": datetime.now().isoformat(),
                }

        embed_thread = threading.Thread(target=_embed_worker, daemon=True)
        embed_thread.start()

        while self._running:
            if not (self._cap and self._cap.isOpened()):
                time.sleep(0.3)
                self._open(self._source)
                continue

            ret, frame = self._cap.read()
            if not ret or frame is None:
                time.sleep(0.3)
                self._open(self._source)
                continue

            # Store raw frame for enrollment / one-shot verify
            with self._frame_lock:
                self._frame = frame
                self._last_read = time.monotonic()

            # ── YuNet detect (mỗi frame, ~5-10ms) ────────────
            faces = detector.detect(frame)
            direction = faces[0]["direction"] if faces else None

            # ── InsightFace embed (background, mỗi EMBED_EVERY frame) ─
            self._embed_counter += 1
            if self._enable_recognition and self._embed_counter % self._embed_every == 0:
                if embed_frame_box[0] is None:
                    embed_frame_box[0] = frame.copy()

            # ── Merge embed result ────────────────────────────
            er = embed_result_box[0]
            matched = er.get("matched", False)
            name    = er.get("name", None)
            score   = er.get("score", 0.0)
            crop    = er.get("face_crop", None)
            ts      = er.get("timestamp", datetime.now().isoformat())

            # ── Annotate frame 1 lần ──────────────────────────
            label = name if matched else ("Detecting..." if faces else "No Face")
            out   = annotate_faces(frame.copy(), faces, label, matched, score)

            # Resize để giảm encode time
            h, w = out.shape[:2]
            if w > 640:
                out = cv2.resize(out, (640, int(h * 640 / w)))

            _, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 65])
            jpeg = buf.tobytes()

            with self._jpeg_lock:
                self._annotated_jpeg = jpeg

            with self._recog_lock:
                self._recog.update({
                    "matched": matched, "name": name, "score": score,
                    "direction": direction, "face_count": len(faces),
                    "face_crop": crop, "timestamp": ts,
                })

            # ~100fps read, consumers throttle themselves
            time.sleep(0.008)

        embed_running.clear()

    # ── Public API ────────────────────────────────────────────
    def start(self, source) -> bool:
        if self._running and self._source == source:
            return True
        self.stop()
        self._source = source
        if not self._open(source):
            return False
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap:
            self._cap.release()
            self._cap = None
        self._frame = None
        self._annotated_jpeg = None
        self._source = None

    def get_frame(self) -> Optional[np.ndarray]:
        """Raw frame cho enrollment/verify one-shot."""
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    def get_annotated_jpeg(self) -> Optional[bytes]:
        """Pre-encoded annotated JPEG cho /webcam và /stream."""
        with self._jpeg_lock:
            return self._annotated_jpeg

    def get_recognition_result(self) -> dict:
        """Latest recognition result."""
        with self._recog_lock:
            return dict(self._recog)

    def enable_recognition(self, stored_embeddings: list, threshold: float = 0.5, embed_every: int = 8):
        """Bật nhận diện realtime. stored_embeddings = [(username, np.ndarray)]"""
        self._stored_embeddings = stored_embeddings
        self._face_threshold    = threshold
        self._embed_every       = embed_every
        self._enable_recognition = True

    def disable_recognition(self):
        self._enable_recognition = False
        self._stored_embeddings  = []

    @property
    def is_running(self) -> bool:
        return self._running


camera_manager = CameraManager()
