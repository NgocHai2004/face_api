"""
WebSocket endpoint cho Consumers.
URL: ws://host:8000/ws/consumer?topic=security
     ws://host:8000/ws/consumer?topic=*          (nhận tất cả topics)
     ws://host:8000/ws/consumer                   (mặc định: nhận tất cả)

Consumer kết nối, Hub tự động push event chuẩn hóa realtime.
"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.event_bus import event_bus, WILDCARD_TOPIC

logger = logging.getLogger("event_hub.ws_consumer")
router = APIRouter()


@router.websocket("/ws/consumer")
async def consumer_ws(websocket: WebSocket, topic: str = WILDCARD_TOPIC):
    """
    WebSocket endpoint dành cho consumers.
    - `topic` query param: topic cụ thể hoặc '*' để nhận tất cả.
    - Ngay khi kết nối, nhận lịch sử events gần nhất của topic đó.
    - Sau đó nhận realtime khi có event mới.
    """
    await websocket.accept()
    client = websocket.client
    logger.info("Consumer connected: %s topic=%s", client, topic)

    # Gửi thông báo kết nối thành công
    await websocket.send_json({
        "type": "__system__",
        "status": "connected",
        "subscribed_topic": topic,
        "message": f"Subscribed to topic: {topic}. Receiving live events.",
    })

    # Subscribe vào EventBus (cũng gửi history ngay lập tức)
    await event_bus.subscribe(websocket, topic)

    try:
        # Giữ connection alive - chờ disconnect từ client
        # Consumer không cần gửi gì, chỉ nhận
        while True:
            # Nhận ping/message từ client (nếu có) để giữ connection
            msg = await websocket.receive_text()
            # Cho phép client gửi lệnh đổi topic
            if msg.strip().startswith("{"):
                import json
                try:
                    cmd = json.loads(msg)
                    if cmd.get("action") == "change_topic":
                        new_topic = cmd.get("topic", WILDCARD_TOPIC)
                        # Unsubscribe topic cũ
                        event_bus.unsubscribe(websocket, topic)
                        topic = new_topic
                        # Subscribe topic mới
                        await event_bus.subscribe(websocket, topic)
                        await websocket.send_json({
                            "type": "__system__",
                            "status": "topic_changed",
                            "subscribed_topic": topic,
                        })
                except Exception:
                    pass  # Bỏ qua lệnh không hợp lệ

    except WebSocketDisconnect:
        logger.info("Consumer disconnected: %s topic=%s", client, topic)
    except Exception as exc:
        logger.exception("Consumer WS error: %s", exc)
    finally:
        event_bus.unsubscribe_all(websocket)
