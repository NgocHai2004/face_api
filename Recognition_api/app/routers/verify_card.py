"""
app/routers/verify_card.py

Xác thực bằng thẻ NFC/RFID — song song với face verify.

Endpoint:
  POST /verify/card?card_id=A66AB0AA
    → Lookup user theo card_id trong MongoDB
    → Push WebSocket event lên App_center (giống verify_matched)
    → Trả về kết quả JSON

Flow thực tế:
  - NFC module quét thẻ → gọi POST /verify/card?card_id=XXX
  - Recognition API lookup user → push socket → trả JSON
  - NFC module (hoặc UI) hiển thị kết quả
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.database import User
from app.ws_producer import push_event_async

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verify", tags=["Verify Card"])


@router.post(
    "/card",
    summary="💳 Xác thực bằng thẻ NFC/RFID",
    description=(
        "NFC reader module gọi endpoint này khi quét được thẻ trong chế độ xác thực.\n\n"
        "**Flow:**\n"
        "1. Lookup user theo `card_id` trong MongoDB\n"
        "2. Kiểm tra hết hạn (nếu có `expiry_date`)\n"
        "3. Push WebSocket event lên App_center (event `verify_card_matched` hoặc `verify_card_failed`)\n"
        "4. Trả về JSON kết quả\n\n"
        "**Gọi từ NFC module:** `POST /verify/card?card_id=A66AB0AA`"
    ),
)
async def verify_card(
    card_id: str = Query(..., description="UID thẻ NFC dạng hex in hoa (VD: A66AB0AA)"),
):
    card_id_upper = card_id.upper().strip()
    if not card_id_upper:
        raise HTTPException(status_code=422, detail="card_id không được để trống")

    ts = datetime.now().isoformat()

    # Lookup user theo card_id
    user = await User.find_one(User.card_id == card_id_upper)

    if user is None:
        # Thẻ không tồn tại trong DB → push event failed
        logger.info(f"[verify_card] FAILED card_not_found: {card_id_upper}")
        fail_event = {
            "event":    "verify_card_failed",
            "type":     "card_verify",
            "status":   "failed",
            "card_id":  card_id_upper,
            "username": None,
            "position": "",
            "matched":  False,
            "reason":   "card_not_found",
            "timestamp": ts,
            "message":  f"❌ Thẻ {card_id_upper} không tìm thấy trong hệ thống",
        }
        asyncio.ensure_future(push_event_async(fail_event, event_type="card_verify"))
        return JSONResponse(
            status_code=404,
            content={**fail_event},
        )

    # Kiểm tra hết hạn
    expired = False
    if user.expiry_date and user.expiry_date < datetime.utcnow():
        expired = True

    if expired:
        # Thẻ hết hạn → push event failed
        logger.info(f"[verify_card] FAILED expired: {card_id_upper} user={user.username}")
        expire_event = {
            "event":       "verify_card_failed",
            "type":        "card_verify",
            "status":      "failed",
            "card_id":     card_id_upper,
            "username":    user.username,
            "position":    user.position or "",
            "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
            "matched":     False,
            "reason":      "expired",
            "timestamp":   ts,
            "message":     f"❌ Thẻ của {user.username} đã hết hạn",
        }
        asyncio.ensure_future(push_event_async(expire_event, event_type="card_verify"))
        return JSONResponse(
            status_code=403,
            content={**expire_event},
        )

    # Xác thực thành công
    event_data = {
        "event":       "verify_card_matched",
        "type":        "card_verify",
        "status":      "success",
        "card_id":     card_id_upper,
        "username":    user.username,
        "position":    user.position or "",
        "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
        "matched":     True,
        "reason":      "ok",
        "timestamp":   ts,
        "message":     f"✅ Xác thực thẻ thành công: {user.username}",
    }
    asyncio.ensure_future(push_event_async(event_data, event_type="card_verify"))
    logger.info(f"[verify_card] MATCHED: {card_id_upper} → {user.username}")

    return {
        "success":     True,
        "matched":     True,
        "card_id":     card_id_upper,
        "username":    user.username,
        "position":    user.position or "",
        "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
        "reason":      "ok",
        "timestamp":   ts,
        "message":     f"✅ Xác thực thành công: {user.username} ({user.position or 'N/A'})",
    }
