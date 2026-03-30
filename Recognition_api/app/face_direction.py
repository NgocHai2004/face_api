"""
face_direction.py — Xác định hướng mặt dùng InsightFace pose (yaw từ buffalo_l).

InsightFace buffalo_l trả về face.pose = [pitch, yaw, roll] (degrees).
Gọi model.get() 1 lần → dùng kết quả cho cả detect hướng lẫn embedding.

Phân loại yaw:
  |yaw| ≤ THANG_MAX                → THANG  (nhìn thẳng)
  yaw   > SIDE_MIN                 → TRAI   (quay trái rõ)
  yaw   < -SIDE_MIN                → PHAI   (quay phải rõ)
  THANG_MAX < |yaw| < SIDE_MIN     → None   (vùng mờ, bỏ qua)
"""
import cv2
import numpy as np
from app.face_utils import get_face_model

THANG_MAX = 12.0   # ° — lệch < 12° → vẫn THANG
SIDE_MIN  = 20.0   # ° — phải nghiêng ≥ 20° mới nhận TRAI/PHAI

# ROI zone: hình chữ nhật 112×98px ở giữa frame
ROI_W = 300
ROI_H = 400


def _get_roi(frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Trả về (x1, y1, x2, y2) của ROI zone ở giữa frame."""
    cx, cy = frame_w // 2, frame_h // 2
    x1 = cx - ROI_W // 2
    y1 = cy - ROI_H // 2
    x2 = x1 + ROI_W
    y2 = y1 + ROI_H
    return x1, y1, x2, y2


def _face_in_roi(bbox, roi: tuple[int, int, int, int]) -> bool:
    """Kiểm tra tâm bbox khuôn mặt có nằm trong ROI không."""
    fx1, fy1, fx2, fy2 = [int(v) for v in bbox]
    fcx = (fx1 + fx2) // 2
    fcy = (fy1 + fy2) // 2
    rx1, ry1, rx2, ry2 = roi
    return rx1 <= fcx <= rx2 and ry1 <= fcy <= ry2


def draw_roi(frame: np.ndarray, in_zone: bool = False) -> np.ndarray:
    """Vẽ ROI zone lên frame — xanh nếu mặt trong zone, đỏ nếu chưa."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = _get_roi(w, h)
    color = (0, 255, 0) if in_zone else (0, 0, 255)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(frame, "HAY DUNG VAO KHUNG", (x1, y1 - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return frame


def _yaw_from_kps(kps) -> float:
    """
    Fallback: tính yaw từ 5 landmarks khi pose không có.
    kps[0]=left_eye, kps[1]=right_eye, kps[2]=nose (InsightFace order)
    """
    if kps is None or len(kps) < 3:
        return 0.0
    le, re, nose = kps[0], kps[1], kps[2]
    eye_mid_x = (le[0] + re[0]) / 2
    eye_width  = abs(re[0] - le[0])
    if eye_width < 1:
        return 0.0
    # Dương → mũi lệch phải (nhìn từ camera) → người quay TRÁI
    return (nose[0] - eye_mid_x) / eye_width * 45.0


def _classify_yaw(yaw: float):
    if yaw > SIDE_MIN:
        return "TRAI",  (0, 165, 255)    # cam
    elif yaw < -SIDE_MIN:
        return "PHAI",  (255, 80, 0)     # xanh đậm
    elif abs(yaw) <= THANG_MAX:
        return "THANG", (0, 210, 60)     # xanh lá
    else:
        return None, (180, 180, 180)     # xám — vùng mờ


def get_face_direction(frame: np.ndarray) -> dict:
    """
    Phân tích hướng khuôn mặt dùng InsightFace (buffalo_l face.pose).
    Gọi model.get() 1 lần — kết quả dùng cho cả detect hướng lẫn embedding.

    Returns:
        {
            "direction": "THANG" | "TRAI" | "PHAI" | None,
            "yaw": float,
            "pitch": float,
            "roll": float,
            "annotated_frame": np.ndarray,
            "_faces": list,   # kết quả InsightFace thô — dùng lại cho embedding
        }
    """
    model = get_face_model()
    faces = model.get(frame)
    annotated = frame.copy()
    h, w = frame.shape[:2]
    roi = _get_roi(w, h)

    _no_result = {"direction": None, "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
                  "annotated_frame": annotated, "_faces": [], "in_roi": False}

    if not faces:
        draw_roi(annotated, in_zone=False)
        cv2.putText(annotated, "Khong phat hien khuon mat",
                    (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return _no_result

    # ── Lọc khuôn mặt trong ROI ───────────────────────────────
    faces_in_roi = [f for f in faces if _face_in_roi(f.bbox, roi)]

    if not faces_in_roi:
        # Vẽ tất cả khuôn mặt ngoài zone bằng màu xám
        draw_roi(annotated, in_zone=False)
        for f in faces:
            x1, y1, x2, y2 = [int(v) for v in f.bbox]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (120, 120, 120), 1)
        cv2.putText(annotated, "Hay dung vao khung",
                    (w//2 - 90, h//2 + 60), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
        return {**_no_result, "annotated_frame": annotated}

    # Chọn khuôn mặt lớn nhất trong ROI
    face = max(faces_in_roi, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))

    # ── Lấy yaw từ pose (buffalo_l cung cấp) ─────────────────
    pitch = yaw = roll = 0.0
    if hasattr(face, "pose") and face.pose is not None and len(face.pose) >= 3:
        pitch, yaw, roll = float(face.pose[0]), float(face.pose[1]), float(face.pose[2])
    else:
        yaw = _yaw_from_kps(face.kps)

    direction, color = _classify_yaw(yaw)

    # ── Vẽ ROI zone (xanh = mặt trong zone) ─────────────────
    draw_roi(annotated, in_zone=True)

    # ── Vẽ bbox + thông tin ──────────────────────────────────
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

    label = direction if direction else f"?({yaw:+.1f})"
    cv2.putText(annotated,
                f"{label}  yaw={yaw:+.1f}",
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    cv2.putText(annotated,
                f"p={pitch:.0f} r={roll:.0f}",
                (x1, max(y1 - 32, 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)

    if face.kps is not None:
        for pt in face.kps:
            cv2.circle(annotated, (int(pt[0]), int(pt[1])), 3, (0, 255, 255), -1)

    return {
        "direction": direction,
        "yaw": round(yaw, 2),
        "pitch": round(pitch, 2),
        "roll": round(roll, 2),
        "annotated_frame": annotated,
        "_faces": faces_in_roi,   # chỉ trả về faces trong ROI
        "in_roi": True,
    }
