"""
app/routers/verify_finger.py

Xác thực bằng vân tay R305 — song song với face/card verify.

Endpoint:
  POST /verify/finger?finger_id=3&confidence=150
    → Lookup user theo finger_id trong MongoDB
    → Kiểm tra expiry
    → Push WebSocket event lên App_center
    → Trả về kết quả JSON

Flow thực tế:
  - Finger reader quét vân tay → searchTemplate() → gọi POST /verify/finger?finger_id=3&confidence=150
  - Recognition API lookup user → push socket → trả JSON
  - Finger reader (hoặc UI) hiển thị kết quả
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

router = APIRouter(prefix="/verify", tags=["Verify Finger"])


@router.post(
    "/finger",
    summary="🖐 Xác thực bằng vân tay R305",
    description=(
        "Finger reader module gọi endpoint này khi quét được vân tay trong chế độ xác thực.\n\n"
        "**Flow:**\n"
        "1. Lookup user theo `finger_id` trong MongoDB\n"
        "2. Kiểm tra hết hạn (nếu có `expiry_date`)\n"
        "3. Push WebSocket event lên App_center (`verify_finger_matched` hoặc `verify_finger_failed`)\n"
        "4. Trả về JSON kết quả\n\n"
        "**Gọi từ finger reader:** `POST /verify/finger?finger_id=3&confidence=150`"
    ),
)
async def verify_finger(
    finger_id:  int = Query(..., ge=0, le=161, description="Slot ID trên cảm biến R305 (0–161)"),
    confidence: int = Query(default=0, ge=0, description="Confidence score từ searchTemplate()"),
):
    ts = datetime.now().isoformat()

    # Lookup user theo finger_id
    user = await User.find_one(User.finger_id == finger_id)

    if user is None:
        logger.info(f"[verify_finger] FAILED finger_not_found: finger_id={finger_id}")
        fail_event = {
            "event":      "verify_finger_failed",
            "type":       "finger_verify",
            "status":     "failed",
            "finger_id":  finger_id,
            "username":   None,
            "position":   "",
            "matched":    False,
            "confidence": confidence,
            "reason":     "finger_not_found",
            "timestamp":  ts,
            "message":    f"❌ Vân tay slot {finger_id} không tìm thấy trong hệ thống",
        }
        asyncio.ensure_future(push_event_async(fail_event, event_type="finger_verify"))
        return JSONResponse(
            status_code=404,
            content={**fail_event},
        )

    # Kiểm tra hết hạn
    expired = False
    if user.expiry_date and user.expiry_date < datetime.utcnow():
        expired = True

    if expired:
        logger.info(
            f"[verify_finger] FAILED expired: finger_id={finger_id} user={user.username}"
        )
        expire_event = {
            "event":       "verify_finger_failed",
            "type":        "finger_verify",
            "status":      "failed",
            "finger_id":   finger_id,
            "username":    user.username,
            "position":    user.position or "",
            "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
            "matched":     False,
            "confidence":  confidence,
            "reason":      "expired",
            "timestamp":   ts,
            "message":     f"❌ Vân tay của {user.username} đã hết hạn",
        }
        asyncio.ensure_future(push_event_async(expire_event, event_type="finger_verify"))
        return JSONResponse(
            status_code=403,
            content={**expire_event},
        )

    # Xác thực thành công
    event_data = {
        "event":       "verify_finger_matched",
        "type":        "finger_verify",
        "status":      "success",
        "finger_id":   finger_id,
        "username":    user.username,
        "position":    user.position or "",
        "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
        "matched":     True,
        "confidence":  confidence,
        "reason":      "ok",
        "timestamp":   ts,
        "message":     f"✅ Xác thực vân tay thành công: {user.username}",
    }
    asyncio.ensure_future(push_event_async(event_data, event_type="finger_verify"))
    logger.info(f"[verify_finger] MATCHED: finger_id={finger_id} → {user.username}")

    return {
        "success":     True,
        "matched":     True,
        "finger_id":   finger_id,
        "username":    user.username,
        "position":    user.position or "",
        "expiry_date": user.expiry_date.isoformat() if user.expiry_date else None,
        "confidence":  confidence,
        "reason":      "ok",
        "timestamp":   ts,
        "message":     f"✅ Xác thực thành công: {user.username} ({user.position or 'N/A'})",
    }
