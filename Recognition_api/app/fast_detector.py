"""
fast_detector.py — Detector khuôn mặt nhanh dùng OpenCV YuNet (cv2.FaceDetectorYN)

YuNet: ~5-10ms/frame trên Pi CPU → đủ chạy 15fps
InsightFace: ~150-400ms → chỉ gọi mỗi N frame để lấy embedding

Phân loại hướng mặt từ landmarks YuNet (không cần InsightFace):
  YuNet trả về 5 landmarks: right_eye, left_eye, nose_tip, right_corner_mouth, left_corner_mouth
  Tính yaw từ tỷ lệ khoảng cách mắt-mũi
"""
import cv2
import numpy as np
from typing import Optional

# Đồng bộ ngưỡng với face_direction.py
THANG_MAX = 12.0   # ° — lệch < 12° → THANG
SIDE_MIN  = 20.0   # ° — phải nghiêng ≥ 20° mới nhận TRAI/PHAI


class YuNetDetector:
    """Singleton wrapper cho cv2.FaceDetectorYN (YuNet)."""

    def __init__(self, input_size=(320, 240), score_threshold=0.6, nms_threshold=0.3):
        self._det: Optional[cv2.FaceDetectorYN] = None
        self._input_size = input_size
        self._score_threshold = score_threshold
        self._nms_threshold = nms_threshold
        self._init()

    def _init(self):
        try:
            self._det = cv2.FaceDetectorYN.create(
                model="",           # dùng built-in model
                config="",
                input_size=self._input_size,
                score_threshold=self._score_threshold,
                nms_threshold=self._nms_threshold,
                top_k=5,
                backend_id=cv2.dnn.DNN_BACKEND_OPENCV,
                target_id=cv2.dnn.DNN_TARGET_CPU,
            )
            print("[YuNet] Loaded built-in model")
        except Exception as e:
            print(f"[YuNet] Failed to init: {e} — will use Haar fallback")
            self._det = None

    def detect(self, frame: np.ndarray) -> list[dict]:
        """
        Returns list of face dicts:
          {bbox: [x,y,w,h], landmarks: [(x,y)×5], score: float,
           direction: 'THANG'|'TRAI'|'PHAI', yaw: float}
        """
        if self._det is None:
            return self._haar_detect(frame)

        h, w = frame.shape[:2]
        # YuNet cần input_size khớp với frame
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(frame)

        results = []
        if faces is None:
            return results

        for face in faces:
            # face = [x, y, w, h, lm0x,lm0y, ..., lm4x,lm4y, score]
            x, y, bw, bh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
            score = float(face[-1])
            lms = [(float(face[4 + i*2]), float(face[5 + i*2])) for i in range(5)]
            # YuNet landmark order: right_eye(0), left_eye(1), nose(2), right_mouth(3), left_mouth(4)
            direction, yaw = _calc_direction(lms)
            results.append({
                "bbox": [x, y, bw, bh],
                "landmarks": lms,
                "score": score,
                "direction": direction,
                "yaw": yaw,
            })
        return results

    def _haar_detect(self, frame: np.ndarray) -> list[dict]:
        """Fallback Haar Cascade nếu YuNet không khởi tạo được."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))
        results = []
        for (x, y, w, h) in faces:
            results.append({
                "bbox": [int(x), int(y), int(w), int(h)],
                "landmarks": [],
                "score": 1.0,
                "direction": "THANG",
                "yaw": 0.0,
            })
        return results


def _calc_direction(lms: list) -> tuple[str, float]:
    """
    lms[0]=right_eye, lms[1]=left_eye, lms[2]=nose_tip
    Tính yaw proxy từ offset mũi so với tâm 2 mắt.
    """
    if len(lms) < 3:
        return "THANG", 0.0

    re, le, nose = lms[0], lms[1], lms[2]
    eye_mid_x  = (re[0] + le[0]) / 2
    eye_width  = abs(le[0] - re[0])
    if eye_width < 1:
        return "THANG", 0.0

    # Chuẩn hoá offset → scale sang ~[-45,+45] độ
    # Dương → mũi lệch phải camera → người quay TRÁI
    yaw = (nose[0] - eye_mid_x) / eye_width * 45.0
    if yaw > SIDE_MIN:
        return "TRAI", yaw
    elif yaw < -SIDE_MIN:
        return "PHAI", yaw
    elif abs(yaw) <= THANG_MAX:
        return "THANG", yaw
    # Vùng mờ → None
    return None, yaw


# Singleton
_yunet: Optional[YuNetDetector] = None


def get_fast_detector() -> YuNetDetector:
    global _yunet
    if _yunet is None:
        _yunet = YuNetDetector()
    return _yunet


def annotate_faces(frame: np.ndarray, faces: list[dict], label: str = "", matched: bool = False, score: float = 0.0) -> np.ndarray:
    """Vẽ bbox + landmarks + hướng lên frame."""
    out = frame.copy()
    color_match   = (0, 220, 0)
    color_unknown = (0, 0, 220)
    color_dir     = (0, 200, 255)

    for i, face in enumerate(faces):
        x, y, w, h = face["bbox"]
        color = color_match if (matched and i == 0) else color_unknown
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)

        # Tên + score
        if i == 0 and label:
            text = f"{label} {score:.0%}" if score > 0 else label
            cv2.putText(out, text, (x, max(y - 8, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

        # Hướng mặt
        direction = face.get("direction", "")
        if direction:
            cv2.putText(out, direction, (x, y + h + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color_dir, 1)

        # 5 landmarks
        for lm in face.get("landmarks", []):
            cv2.circle(out, (int(lm[0]), int(lm[1])), 2, (0, 255, 255), -1)

    return out
