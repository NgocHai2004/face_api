"""
app/routers/enroll_finger.py

Gán finger_id (slot R305) vào user MongoDB.

Endpoint:
  POST /enroll/finger/id?username=alice&finger_id=3
    → Lookup user → check conflict → save → return result

Response codes:
  200 — thành công
  404 — user không tìm thấy
  409 — finger_id đã thuộc user khác
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.database import User
from app.ws_producer import push_event_async

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enroll/finger", tags=["Enroll Fingerprint"])


@router.post(
    "/id",
    summary="🖐 Gán finger_id vào user",
    description=(
        "Finger reader module gọi endpoint này sau khi enroll vân tay thành công trên R305.\n\n"
        "**Flow:**\n"
        "1. Lookup user theo `username` trong MongoDB\n"
        "2. Kiểm tra conflict: `finger_id` đã thuộc user khác chưa?\n"
        "3. Lưu `finger_id` vào `UserDocument`\n"
        "4. Push WebSocket event `enroll_finger_done` lên App_center\n"
        "5. Trả về JSON kết quả\n\n"
        "**Gọi từ finger reader:** `POST /enroll/finger/id?username=alice&finger_id=3`"
    ),
)
async def enroll_finger_id(
    username:  str = Query(..., description="Username của người dùng"),
    finger_id: int = Query(..., ge=0, le=161, description="Slot ID trên cảm biến R305 (0–161)"),
):
    # Lookup user
    user = await User.find_one(User.username == username)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' không tồn tại")

    # Kiểm tra conflict: finger_id dùng bởi user khác?
    conflict = await User.find_one(User.finger_id == finger_id)
    if conflict is not None and conflict.username != username:
        logger.warning(
            f"[enroll_finger] CONFLICT finger_id={finger_id} đã thuộc '{conflict.username}'"
        )
        return JSONResponse(
            status_code=409,
            content={
                "success":   False,
                "username":  username,
                "finger_id": finger_id,
                "message":   (
                    f"❌ finger_id {finger_id} đã được đăng ký bởi user '{conflict.username}'"
                ),
            },
        )

    # Lưu finger_id
    user.finger_id = finger_id
    await user.save()

    logger.info(f"[enroll_finger] OK finger_id={finger_id} → user='{username}'")

    event_data = {
        "event":     "enroll_finger_done",
        "type":      "finger_enroll",
        "status":    "success",
        "username":  username,
        "finger_id": finger_id,
        "message":   f"✅ Đăng ký vân tay thành công: {username} (slot {finger_id})",
    }
    asyncio.ensure_future(push_event_async(event_data, event_type="finger_enroll"))

    return {
        "success":   True,
        "username":  username,
        "finger_id": finger_id,
        "message":   f"✅ Đã gán finger_id={finger_id} cho user '{username}'",
    }
