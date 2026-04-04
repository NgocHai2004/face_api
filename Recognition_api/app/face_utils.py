import insightface
import numpy as np
import cv2
import os
import pickle
from app.config import settings

_app_model = None


def get_face_model():
    global _app_model
    if _app_model is None:
        _app_model = insightface.app.FaceAnalysis(name=settings.INSIGHTFACE_MODEL)
        det_size = getattr(settings, "DET_SIZE", 320)
        try:
            det_size = int(det_size)
        except (ValueError, TypeError):
            det_size = 320
        _app_model.prepare(ctx_id=0, det_size=(det_size, det_size))
        print(f"[InsightFace] det_size={det_size}x{det_size}")
    return _app_model


def extract_embedding_from_image(image: np.ndarray):
    """
    Returns (normalized_embedding, face_crop_bgr) or (None, None).
    Embedding đã được L2-normalize → cosine similarity = np.dot(a, b).
    """
    model = get_face_model()
    faces = model.get(image)
    if not faces:
        return None, None
    largest = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    emb = largest.embedding
    # L2-normalize tại chỗ
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm

    # Crop khuôn mặt
    x1, y1, x2, y2 = [int(v) for v in largest.bbox]
    h, w = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    face_crop = image[y1:y2, x1:x2]

    return emb, face_crop


def embedding_from_faces(image: np.ndarray, faces: list):
    """
    Tái sử dụng kết quả InsightFace đã có (từ _faces trong get_face_direction).
    Tránh gọi model.get() 2 lần cho cùng 1 frame.
    Returns (normalized_embedding, face_crop_bgr) or (None, None).
    """
    if not faces:
        return None, None
    largest = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    emb = largest.embedding
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm

    x1, y1, x2, y2 = [int(v) for v in largest.bbox]
    h, w = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    face_crop = image[y1:y2, x1:x2]
    return emb, face_crop


def embedding_to_bytes(embedding: np.ndarray) -> bytes:
    # Lưu dưới dạng float32 để tiết kiệm không gian và tăng tốc deserialize
    return pickle.dumps(embedding.astype(np.float32))


def bytes_to_embedding(data: bytes) -> np.ndarray:
    return pickle.loads(data).astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    # Vì embedding đã normalize, cosine = dot product
    return float(np.dot(a, b))


def verify_faces(stored_embedding: np.ndarray, query_embedding: np.ndarray, label: str = "") -> tuple[bool, float]:
    score = cosine_similarity(stored_embedding, query_embedding)
    matched = score >= settings.FACE_THRESHOLD
    tag = f"user={label}" if label else "(no label)"
    print(
        f"[verify_faces] {tag} score={score:.4f} threshold={settings.FACE_THRESHOLD:.2f}"
        f" → {'MATCH ✅' if matched else 'NO_MATCH ❌'}",
        flush=True,
    )
    return matched, score


def save_face_image(username: str, image: np.ndarray) -> str:
    path = os.path.join(settings.FACE_IMAGES_DIR, f"{username}.jpg")
    cv2.imwrite(path, image)
    return path


def crop_to_base64(image: np.ndarray, bbox) -> str | None:
    """Crop khuôn mặt từ bbox và encode sang base64 JPEG."""
    import base64
    try:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = image.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.b64encode(buf.tobytes()).decode()
    except Exception:
        return None
