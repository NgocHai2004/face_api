import asyncio
import json
import logging
import threading
import websockets

logger = logging.getLogger(__name__)

WS_PRODUCER_URL = "ws://localhost:8000/ws/producer"


def _build_message(payload: dict) -> dict:
    """
    Wrap verify result thành format Hub yêu cầu:
    {
        "source": "face_recognition_api",
        "type":   "face_recognition",
        "priority": "high",
        "payload": { ...dữ liệu xác thực... }
    }
    """
    return {
        "source": "face_recognition_api",
        "type": "face_recognition",
        "priority": "high",
        "payload": payload,
    }


async def push_event_async(payload: dict):
    """
    Async — dùng với asyncio.ensure_future() hoặc await bên trong async route.
    """
    message = _build_message(payload)
    try:
        async with websockets.connect(WS_PRODUCER_URL, open_timeout=5) as ws:
            await ws.send(json.dumps(message, ensure_ascii=False))
            # Đọc response từ Hub (nếu có)
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=3)
                logger.info(f"[WS] Hub response: {resp}")
            except asyncio.TimeoutError:
                pass
            logger.info(
                f"[WS] Pushed face_recognition event: "
                f"username={payload.get('username')} matched={payload.get('matched')}"
            )
    except Exception as e:
        logger.warning(f"[WS] Push failed (Hub may be offline): {e}")


def push_event(payload: dict):
    """
    Sync — fire-and-forget từ sync code.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(push_event_async(payload))
    except RuntimeError:
        def _run():
            asyncio.run(push_event_async(payload))
        threading.Thread(target=_run, daemon=True).start()
