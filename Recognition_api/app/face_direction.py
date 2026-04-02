"""
face_direction.py — Xác định hướng mặt dùng InsightFace pose (yaw từ buffalo_l).

InsightFace buffalo_l trả về face.pose = [pitch, yaw, roll] (degrees).
Gọi model.get() 1 lần → dùng kết quả cho cả detect hướng lẫn embedding.

Phân loại yaw:
  |yaw| ≤ THANG_MAX                → THANG  (nhìn thẳng)
  yaw   > SIDE_MIN                 → TRAI   (quay trái rõ)
  yaw   < -SIDE_MIN                → PHAI   (quay phải rõ)
  THANG_MAX < |yaw| < SIDE_MIN     → None   (vùng mờ, bỏ qua)

Không dùng ROI zone — nhận mặt ở bất kỳ vị trí nào trong frame.
"""
import cv2
import numpy as np
from app.face_utils import get_face_model

THANG_MAX = 12.0   # ° — lệch < 12° → vẫn THANG
SIDE_MIN  = 20.0   # ° — phải nghiêng ≥ 20° mới nhận TRAI/PHAI


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
    Nhận mặt ở bất kỳ vị trí nào trong frame (không giới hạn ROI zone).

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

    _no_result = {"direction": None, "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
                  "annotated_frame": annotated, "_faces": []}

    if not faces:
        cv2.putText(annotated, "Khong phat hien khuon mat",
                    (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        return _no_result

    # Chọn khuôn mặt lớn nhất trong toàn frame
    face = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))

    # ── Lấy yaw từ pose (buffalo_l cung cấp) ─────────────────
    pitch = yaw = roll = 0.0
    if hasattr(face, "pose") and face.pose is not None and len(face.pose) >= 3:
        pitch, yaw, roll = float(face.pose[0]), float(face.pose[1]), float(face.pose[2])
    else:
        yaw = _yaw_from_kps(face.kps)

    direction, color = _classify_yaw(yaw)

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
        "_faces": faces,   # trả về tất cả faces để embedding dùng face lớn nhất
    }
