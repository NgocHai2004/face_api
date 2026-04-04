"""
app/routers/enroll_finger_scan.py

Proxy SSE đăng ký vân tay từ finger_reader service.

Endpoint:
  GET /enroll/finger/scan?username=alice&person_name=Alice&timeout=60
    → Proxy SSE stream từ finger_reader /api/enroll/scan
    → Khi nhận event "registered" → gán finger_id vào user trong MongoDB

Flow:
  1. Client kết nối SSE tới /enroll/finger/scan
  2. Recognition API mở kết nối SSE tới finger_reader /api/enroll/scan
  3. Stream từng SSE event qua lại cho client
  4. Khi nhận event "registered" từ finger_reader → lưu finger_id vào DB

Environment:
  FINGER_READER_URL — URL tới finger_reader service (default: http://localhost:8082)
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enroll/finger", tags=["Enroll Fingerprint"])

FINGER_READER_URL = settings.FINGER_READER_URL


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _proxy_enroll_sse(
    username: str,
    person_name: str,
    timeout: int,
) -> AsyncGenerator[str, None]:
    """
    Kết nối tới finger_reader SSE enroll rồi forward từng event cho client.
    Khi nhận 'registered' event → lưu finger_id vào MongoDB.
    """
    upstream_url = (
        f"{FINGER_READER_URL}/api/enroll/scan"
        f"?username={username}&person_name={person_name}&timeout={timeout}"
    )

    logger.info(f"[enroll_finger_scan] Proxy SSE → {upstream_url}")

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", upstream_url) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield _sse({
                        "event":   "error",
                        "message": f"finger_reader trả lỗi {resp.status_code}: {body.decode(errors='replace')}",
                    })
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        yield _sse({"event": "error", "message": f"JSON decode error: {raw}"})
                        continue

                    # Forward event về client
                    yield _sse(event)

                    # Sau khi finger_reader báo "registered" thành công,
                    # finger_id đã được lưu vào DB bởi finger_reader (gọi /enroll/finger/id).
                    # Không cần làm gì thêm ở đây.
                    if event.get("event") in ("registered", "error", "register_error"):
                        break

    except httpx.ConnectError:
        yield _sse({
            "event":   "error",
            "message": f"Không kết nối được finger_reader tại {FINGER_READER_URL}. "
                       "Kiểm tra service đang chạy chưa?",
        })
    except httpx.TimeoutException:
        yield _sse({"event": "error", "message": "Timeout kết nối tới finger_reader"})
    except Exception as exc:
        logger.error(f"[enroll_finger_scan] Unexpected error: {exc}", exc_info=True)
        yield _sse({"event": "error", "message": f"Lỗi không xác định: {exc}"})


@router.get(
    "/scan",
    summary="🖐 SSE — Đăng ký vân tay R305 qua finger_reader",
    description=(
        "Proxy SSE stream từ finger_reader để thực hiện quy trình đăng ký vân tay R305 (2 lần quét).\n\n"
        "**Yêu cầu:** finger_reader service phải đang chạy (mặc định tại `http://localhost:8082`).\n\n"
        "**SSE events được forward từ finger_reader:**\n"
        "- `waiting` — Đặt ngón tay lần 1\n"
        "- `finger_placed` — Đã đọc lần 1, nhấc tay\n"
        "- `lift_finger` — Nhấc tay khỏi cảm biến\n"
        "- `second_scan_required` — Đặt ngón tay lần 2\n"
        "- `enrolled` — Template lưu vào sensor thành công\n"
        "- `registered` — finger_id đã gán vào user ✅\n"
        "- `register_error` — Lỗi gán finger_id\n"
        "- `error` — Lỗi chung\n\n"
        "**Ví dụ:**\n"
        "```\n"
        "GET /enroll/finger/scan?username=alice&person_name=Alice Nguyen&timeout=60\n"
        "```"
    ),
)
async def enroll_finger_scan(
    username:    str = Query(..., description="Username đăng ký (phải tồn tại trong DB)"),
    person_name: str = Query(default="", description="Tên hiển thị (để trống = dùng username)"),
    timeout:     int = Query(default=60, ge=10, le=300, description="Timeout mỗi lần quét (giây)"),
):
    return StreamingResponse(
        _proxy_enroll_sse(username, person_name, timeout),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
