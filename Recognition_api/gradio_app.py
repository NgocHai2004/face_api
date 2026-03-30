import gradio as gr
import requests
import numpy as np
import cv2

API_BASE = "http://127.0.0.1:8001"


def numpy_to_jpg_bytes(img: np.ndarray) -> bytes:
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    _, buf = cv2.imencode(".jpg", img_bgr)
    return buf.tobytes()


# ── Tab 1: Đăng ký ───────────────────────────────────────────
def register_face(username: str, image: np.ndarray):
    if not username.strip():
        return "⚠️ Vui lòng nhập tên người dùng."
    if image is None:
        return "⚠️ Vui lòng chụp hoặc tải ảnh lên."

    img_bytes = numpy_to_jpg_bytes(image)
    try:
        resp = requests.post(
            f"{API_BASE}/register",
            params={"username": username.strip()},
            files={"face_image": ("face.jpg", img_bytes, "image/jpeg")},
            timeout=15,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("success"):
            return f"✅ {data['message']} — [{data['username']}]"
        return f"❌ Lỗi: {data.get('detail', data)}"
    except Exception as e:
        return f"❌ Không kết nối được API: {e}"


# ── Tab 2: Xác thực ──────────────────────────────────────────
def verify_face(rtsp_url: str, username: str):
    if not rtsp_url.strip():
        return "⚠️ Vui lòng nhập URL RTSP của camera."

    params = {"rtsp_url": rtsp_url.strip()}
    if username.strip():
        params["username"] = username.strip()

    try:
        resp = requests.post(f"{API_BASE}/verify", params=params, timeout=20)
        data = resp.json()
        if resp.status_code == 200:
            matched = data.get("matched", False)
            name = data.get("username") or "Không xác định"
            score = data.get("similarity", 0.0)
            msg = data.get("message", "")
            icon = "✅" if matched else "❌"
            return (
                f"{icon} {msg}\n\n"
                f"👤 Username  : {name}\n"
                f"📊 Similarity: {score:.4f}\n"
                f"📷 RTSP      : {rtsp_url.strip()}"
            )
        return f"❌ Lỗi API: {data.get('detail', data)}"
    except Exception as e:
        return f"❌ Không kết nối được API: {e}"


# ── Gradio UI ─────────────────────────────────────────────────
with gr.Blocks(title="Face Recognition") as demo:
    gr.Markdown("# 🧠 Face Recognition\nNhận diện khuôn mặt với InsightFace + RTSP")

    with gr.Tabs():

        # Tab Đăng ký
        with gr.Tab("📸 Đăng ký khuôn mặt"):
            gr.Markdown("Upload ảnh hoặc chụp từ webcam để đăng ký khuôn mặt.")
            with gr.Row():
                with gr.Column():
                    reg_username = gr.Textbox(
                        label="Tên người dùng (username)",
                        placeholder="VD: nguyen_van_a",
                    )
                    reg_image = gr.Image(
                        label="Ảnh khuôn mặt",
                        type="numpy",
                        sources=["upload", "webcam"],
                    )
                    reg_btn = gr.Button("✅ Đăng ký", variant="primary")
                with gr.Column():
                    reg_output = gr.Textbox(label="Kết quả", lines=4, interactive=False)

            reg_btn.click(fn=register_face, inputs=[reg_username, reg_image], outputs=reg_output)

        # Tab Xác thực
        with gr.Tab("🔍 Xác thực khuôn mặt"):
            gr.Markdown(
                "Nhập URL RTSP của camera — hệ thống sẽ chụp 1 frame và so khớp khuôn mặt.\n\n"
                "- Nhập **username** để kiểm tra 1 người cụ thể.\n"
                "- Để trống username để **tìm trong toàn bộ DB**."
            )
            with gr.Row():
                with gr.Column():
                    ver_rtsp = gr.Textbox(
                        label="RTSP URL hoặc index camera (0 = webcam local)",
                        placeholder="rtsp://user:pass@192.168.1.100:554/stream1  hoặc  0",
                        lines=1,
                    )
                    ver_username = gr.Textbox(
                        label="Username (tùy chọn — để trống = tìm tất cả)",
                        placeholder="VD: nguyen_van_a",
                    )
                    ver_btn = gr.Button("🔍 Xác thực", variant="primary")
                with gr.Column():
                    ver_output = gr.Textbox(label="Kết quả", lines=7, interactive=False)

            ver_btn.click(fn=verify_face, inputs=[ver_rtsp, ver_username], outputs=ver_output)

    gr.Markdown(f"---\n🖥️ API: `{API_BASE}` | 📖 Docs: [{API_BASE}/docs]({API_BASE}/docs)")


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(),
        ssl_certfile="ssl/cert.pem",
        ssl_keyfile="ssl/key.pem",
        ssl_verify=False,
    )
