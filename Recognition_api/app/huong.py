"""
Face Direction Detection using MediaPipe FaceMesh
Xác định hướng khuôn mặt: TRÁI / PHẢI / THẲNG

Ý tưởng:a
  - Lấy 4 điểm đối xứng trên 2 bên má:
      Má TRÁI  (camera): landmark 234 (tai) và 93  (gò má trên)
      Má PHẢI (camera): landmark 454 (tai) và 323 (gò má trên)
  - Tính độ rộng bên trái  = khoảng cách từ mũi → cạnh má trái
  - Tính độ rộng bên phải = khoảng cách từ mũi → cạnh má phải
  - So sánh: bên nào rộng hơn → mặt quay về bên đó
             bằng nhau (trong ngưỡng) → THẲNG
"""

import cv2
import mediapipe as mp
import numpy as np

# ── Khởi tạo MediaPipe FaceMesh ──────────────────────────────────────────────
mp_face_mesh = mp.solutions.face_mesh
mp_drawing   = mp.solutions.drawing_utils
mp_styles    = mp.solutions.drawing_styles

# ── Ngưỡng phân loại ─────────────────────────────────────────────────────────
# ratio = (right - left) / (right + left)   (tính trên tọa độ gốc, KHÔNG flip)
#
#   Khi mặt nhìn thẳng vào camera:
#       width_left  ≈ width_right  → ratio ≈ 0  → THẲNG
#   Khi mặt quay sang TRÁI (tai phải lộ ra, má trái bị khuất):
#       width_right > width_left   → ratio > 0  → TRÁI  (*)
#   Khi mặt quay sang PHẢI:
#       width_left  > width_right  → ratio < 0  → PHẢI
#
# (*) MediaPipe dùng hệ tọa độ ảnh gốc (không flip), nên
#     landmark 234 nằm bên PHẢI màn hình (= tai phải của người ngồi trước camera).
#     Ta đặt ngược nhãn để khớp với góc nhìn của NGƯỜI DÙNG.
#
# Tăng THRESH để tránh nhận nhầm khi chỉ nghiêng nhẹ.
# Giá trị khuyến nghị: 0.12 – 0.20
THRESH = 0.5 # 25% chênh lệch tương đối — cần nghiêng rõ mới tính là TRÁI/PHẢI

# ── Landmark index ────────────────────────────────────────────────────────────
#
#  Nhìn từ camera:
#
#         [10] trán
#          |
#   [234]--[1]--[454]      234 = tai TRÁI (camera), 454 = tai PHẢI (camera)
#   [93]        [323]      93  = gò má TRÁI,         323 = gò má PHẢI
#          |
#        [152] cằm
#
NOSE_TIP   = 1    # đỉnh mũi (điểm trung tâm chuẩn)

# Cặp điểm má TRÁI (nhìn từ camera)
L_OUTER = 234   # cạnh ngoài má trái (gần tai)
L_INNER = 93    # gò má trái (gần mũi hơn)

# Cặp điểm má PHẢI (nhìn từ camera)
R_OUTER = 454   # cạnh ngoài má phải (gần tai)
R_INNER = 323   # gò má phải (gần mũi hơn)


def px(lm, w, h):
    """Trả về tọa độ pixel 2D của một landmark."""
    return np.array([lm.x * w, lm.y * h])


def segment_length(p1, p2):
    """Độ dài đoạn thẳng 2D."""
    return np.linalg.norm(p1 - p2)


def compute_ratio(landmarks, w, h):
    """
    Tính tỉ lệ chênh lệch giữa 2 bên má.

    Trả về ratio trong [-1, 1]:
      ratio âm  → bên trái (camera) rộng hơn  → mặt quay TRÁI
      ratio dương → bên phải rộng hơn          → mặt quay PHẢI
      gần 0     → cân bằng                      → THẲNG
    """
    nose    = px(landmarks[NOSE_TIP], w, h)
    l_outer = px(landmarks[L_OUTER],  w, h)
    l_inner = px(landmarks[L_INNER],  w, h)
    r_outer = px(landmarks[R_OUTER],  w, h)
    r_inner = px(landmarks[R_INNER],  w, h)

    # Độ rộng mỗi bên = trung bình khoảng cách 2 điểm bên đó tới mũi
    width_left  = (segment_length(nose, l_outer) + segment_length(nose, l_inner)) / 2
    width_right = (segment_length(nose, r_outer) + segment_length(nose, r_inner)) / 2

    total = width_left + width_right
    if total == 0:
        return 0.0

    # ratio = (phải - trái) / tổng
    ratio = (width_right - width_left) / total
    return ratio, width_left, width_right


def classify(ratio):
    """
    Phân loại hướng dựa trên ratio.

    Hệ quy chiếu: góc nhìn của NGƯỜI DÙNG (không flip).
      MediaPipe landmark 234 nằm bên PHẢI ảnh gốc = tai PHẢI người dùng.
      Khi mặt quay TRÁI → tai phải lộ nhiều hơn → width_right > width_left → ratio > 0
      Khi mặt quay PHẢI → tai trái lộ nhiều hơn → width_left  > width_right → ratio < 0
    """
    if ratio > THRESH:
        return "TRAI",  (0, 165, 255)   # cam    — má phải camera rộng hơn → nhìn TRÁI
    elif ratio < -THRESH:
        return "PHAI",  (255,  80,  0)  # xanh lam — má trái camera rộng hơn → nhìn PHẢI
    else:
        return "THANG", (0, 210,  60)   # xanh lá


def draw_cheek_lines(frame, landmarks, w, h, color):
    """Vẽ đoạn thẳng nối 4 điểm má và hiển thị độ rộng."""
    nose    = px(landmarks[NOSE_TIP], w, h).astype(int)
    l_outer = px(landmarks[L_OUTER],  w, h).astype(int)
    l_inner = px(landmarks[L_INNER],  w, h).astype(int)
    r_outer = px(landmarks[R_OUTER],  w, h).astype(int)
    r_inner = px(landmarks[R_INNER],  w, h).astype(int)

    # Vẽ đoạn má trái (xanh lam nhạt)
    cv2.line(frame, tuple(l_outer), tuple(l_inner), (255, 200, 100), 2)
    cv2.line(frame, tuple(l_inner), tuple(nose),    (255, 200, 100), 2)

    # Vẽ đoạn má phải (cam nhạt)
    cv2.line(frame, tuple(r_outer), tuple(r_inner), (100, 200, 255), 2)
    cv2.line(frame, tuple(r_inner), tuple(nose),    (100, 200, 255), 2)

    # Đánh dấu 5 điểm
    for pt, c in [
        (nose,    (0, 255, 255)),
        (l_outer, (255, 180,  50)),
        (l_inner, (255, 180,  50)),
        (r_outer, (50,  180, 255)),
        (r_inner, (50,  180, 255)),
    ]:
        cv2.circle(frame, tuple(pt), 5, c, -1)


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[LỖI] Không mở được webcam.")
        return

    print("=== Face Direction Detection (Cheek Ratio Method) ===")
    print(f"Ngưỡng ratio: < -{THRESH:.2f} → TRÁI | > +{THRESH:.2f} → PHẢI | còn lại → THẲNG")
    print("Nhấn  Q  để thoát.")

    with mp_face_mesh.FaceMesh(
        max_num_faces=2,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as face_mesh:

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            # KHÔNG flip để tọa độ landmark khớp đúng trái/phải
            h, w  = frame.shape[:2]

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            results = face_mesh.process(rgb)
            rgb.flags.writeable = True

            if results.multi_face_landmarks:
                for face_id, face_lm in enumerate(results.multi_face_landmarks):
                    lms = face_lm.landmark

                    # ── Tính ratio và phân loại ──
                    ratio, w_left, w_right = compute_ratio(lms, w, h)
                    direction, color       = classify(ratio)

                    # ── Vẽ mesh nhẹ ──
                    mp_drawing.draw_landmarks(
                        image=frame,
                        landmark_list=face_lm,
                        connections=mp_face_mesh.FACEMESH_CONTOURS,
                        landmark_drawing_spec=None,
                        connection_drawing_spec=mp_styles.get_default_face_mesh_contours_style(),
                    )

                    # ── Vẽ đường nối má ──
                    draw_cheek_lines(frame, lms, w, h, color)

                    # ── Hiển thị nhãn ──
                    y_pos = 40 + face_id * 70
                    cv2.putText(frame,
                                f"[{face_id}] Huong: {direction}",
                                (20, y_pos),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
                    cv2.putText(frame,
                                f"    Ma_trai={w_left:.0f}px  Ma_phai={w_right:.0f}px  ratio={ratio:+.3f}",
                                (20, y_pos + 28),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1, cv2.LINE_AA)
            else:
                cv2.putText(frame, "Khong phat hien khuon mat",
                            (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
                            0.8, (0, 0, 255), 2, cv2.LINE_AA)

            # Legend góc dưới
            cv2.putText(frame,
                        "TRAI=cam  PHAI=xanh lam  THANG=xanh la  |  Q=thoat",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX,
                        0.48, (180, 180, 180), 1, cv2.LINE_AA)

            cv2.imshow("Face Direction  [Q=thoat]", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
