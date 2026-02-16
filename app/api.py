"""REST API routes: auth, devices, user settings, meetings."""
import os
import logging
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import User, Device, UserSettings, Reminder, Meeting
from .auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, hash_token, encrypt_secret, decrypt_secret,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ── Pydantic schemas ──────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str

class DeviceCreate(BaseModel):
    device_id: str
    token: str
    name: str = ""

class DeviceOut(BaseModel):
    id: int
    device_id: str
    name: str
    fw_version: str
    last_seen: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class SettingsUpdate(BaseModel):
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_chat_model: Optional[str] = None
    openai_asr_model: Optional[str] = None
    tts_provider: Optional[str] = None
    openai_tts_model: Optional[str] = None
    openai_tts_voice: Optional[str] = None
    weather_api_key: Optional[str] = None
    weather_city: Optional[str] = None
    tavily_api_key: Optional[str] = None
    youtube_api_key: Optional[str] = None
    notion_token: Optional[str] = None
    notion_database_id: Optional[str] = None

class SettingsOut(BaseModel):
    openai_api_key_set: bool
    openai_base_url: str
    openai_chat_model: str
    openai_asr_model: str
    tts_provider: str
    openai_tts_model: str
    openai_tts_voice: str
    weather_api_key_set: bool
    weather_city: str
    tavily_api_key_set: bool
    youtube_api_key_set: bool
    notion_token_set: bool
    notion_database_id: str


# ── Auth ──────────────────────────────────────────────────────

@router.post("/auth/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(email=req.email, password_hash=hash_password(req.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Create empty settings row
    db.add(UserSettings(user_id=user.id))
    await db.commit()

    token = create_access_token(user.id, user.email)
    logger.info(f"User registered: {user.email} (id={user.id})")
    return TokenResponse(access_token=token, user_id=user.id, email=user.email)


@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, user_id=user.id, email=user.email)


# ── Devices ───────────────────────────────────────────────────

@router.get("/devices", response_model=List[DeviceOut])
async def list_devices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Device).where(Device.user_id == user.id).order_by(Device.created_at.desc())
    )
    return result.scalars().all()


@router.post("/devices", response_model=DeviceOut, status_code=201)
async def add_device(
    req: DeviceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(Device).where(Device.device_id == req.device_id))
    device = existing.scalar_one_or_none()

    if device:
        if device.user_id and device.user_id != user.id:
            raise HTTPException(status_code=400, detail="Device already owned by another user")
        # Re-bind or update token
        device.token_hash = hash_token(req.token)
        device.user_id = user.id
        device.name = req.name or device.name
    else:
        device = Device(
            device_id=req.device_id,
            token_hash=hash_token(req.token),
            user_id=user.id,
            name=req.name,
        )
        db.add(device)

    await db.commit()
    await db.refresh(device)
    logger.info(f"Device {req.device_id} bound to user {user.email}")
    return device


@router.delete("/devices/{device_id}")
async def delete_device(
    device_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Device).where(Device.device_id == device_id, Device.user_id == user.id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.delete(device)
    await db.commit()
    return {"ok": True}


# ── Settings ──────────────────────────────────────────────────

@router.get("/settings", response_model=SettingsOut)
async def get_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    s = result.scalar_one_or_none()
    if not s:
        s = UserSettings(user_id=user.id)
        db.add(s)
        await db.commit()
        await db.refresh(s)

    return SettingsOut(
        openai_api_key_set=bool(s.openai_api_key_enc),
        openai_base_url=s.openai_base_url,
        openai_chat_model=s.openai_chat_model,
        openai_asr_model=s.openai_asr_model,
        tts_provider=s.tts_provider or "",
        openai_tts_model=s.openai_tts_model,
        openai_tts_voice=s.openai_tts_voice,
        weather_api_key_set=bool(s.weather_api_key_enc),
        weather_city=s.weather_city or "",
        tavily_api_key_set=bool(s.tavily_api_key_enc),
        youtube_api_key_set=bool(s.youtube_api_key_enc),
        notion_token_set=bool(s.notion_token_enc),
        notion_database_id=s.notion_database_id or "",
    )


@router.put("/settings")
async def update_settings(
    req: SettingsUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    s = result.scalar_one_or_none()
    if not s:
        s = UserSettings(user_id=user.id)
        db.add(s)

    # Update only provided fields
    if req.openai_api_key is not None:
        s.openai_api_key_enc = encrypt_secret(req.openai_api_key) if req.openai_api_key else ""
    if req.openai_base_url is not None:
        s.openai_base_url = req.openai_base_url
    if req.openai_chat_model is not None:
        s.openai_chat_model = req.openai_chat_model
    if req.openai_asr_model is not None:
        s.openai_asr_model = req.openai_asr_model
    if req.tts_provider is not None:
        s.tts_provider = req.tts_provider
    if req.openai_tts_model is not None:
        s.openai_tts_model = req.openai_tts_model
    if req.openai_tts_voice is not None:
        s.openai_tts_voice = req.openai_tts_voice
    if req.weather_api_key is not None:
        s.weather_api_key_enc = encrypt_secret(req.weather_api_key) if req.weather_api_key else ""
    if req.weather_city is not None:
        s.weather_city = req.weather_city
    if req.tavily_api_key is not None:
        s.tavily_api_key_enc = encrypt_secret(req.tavily_api_key) if req.tavily_api_key else ""
    if req.youtube_api_key is not None:
        s.youtube_api_key_enc = encrypt_secret(req.youtube_api_key) if req.youtube_api_key else ""
    if req.notion_token is not None:
        s.notion_token_enc = encrypt_secret(req.notion_token) if req.notion_token else ""
    if req.notion_database_id is not None:
        s.notion_database_id = req.notion_database_id
    await db.commit()
    logger.info(f"Settings updated for user {user.email}")
    return {"ok": True}


class NotionTestRequest(BaseModel):
    token: str
    database_id: str


@router.post("/settings/notion-test")
async def test_notion(
    req: NotionTestRequest,
    user: User = Depends(get_current_user),
):
    """Test Notion connection with provided token and database ID."""
    try:
        from .tools.builtin.notion import test_connection
        result = await test_connection(req.token, req.database_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Notion connection failed: {e}")


# ── Reminders ────────────────────────────────────────────────

class ReminderOut(BaseModel):
    id: int
    device_id: str
    remind_at: datetime
    message: str
    delivered: int  # 0=pending, 1=delivered, 2=failed
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


@router.get("/reminders", response_model=List[ReminderOut])
async def list_reminders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Reminder)
        .where(Reminder.user_id == user.id)
        .order_by(Reminder.remind_at.desc())
    )
    return result.scalars().all()


@router.delete("/reminders/{reminder_id}")
async def delete_reminder(
    reminder_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Reminder).where(Reminder.id == reminder_id, Reminder.user_id == user.id)
    )
    reminder = result.scalar_one_or_none()
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    await db.delete(reminder)
    await db.commit()
    return {"ok": True}


# ── Meetings ─────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class MeetingOut(BaseModel):
    id: int
    session_id: str
    title: str
    duration_s: int
    status: str
    transcript: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


@router.get("/meetings", response_model=List[MeetingOut])
async def list_meetings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting)
        .where(Meeting.user_id == user.id)
        .order_by(Meeting.started_at.desc())
    )
    return result.scalars().all()


@router.get("/meetings/{meeting_id}/download")
async def download_meeting(
    meeting_id: int,
    token: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Download meeting audio. Accepts token via query param (for browser downloads)."""
    from .auth import decode_access_token
    if not token:
        raise HTTPException(status_code=401, detail="Token required")
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.audio_path:
        raise HTTPException(status_code=404, detail="No audio file available")

    filepath = DATA_DIR / meeting.audio_path
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    filename = f"{meeting.title}_{meeting.session_id}.wav"
    return FileResponse(filepath, media_type="audio/wav", filename=filename)


@router.delete("/meetings/{meeting_id}")
async def delete_meeting(
    meeting_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user.id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Delete audio file
    if meeting.audio_path:
        filepath = DATA_DIR / meeting.audio_path
        if filepath.exists():
            filepath.unlink()
            logger.info(f"Deleted meeting audio: {filepath}")

    await db.delete(meeting)
    await db.commit()
    return {"ok": True}


# ── Conversation History ────────────────────────────────────

@router.get("/devices/{device_id}/conversation")
async def get_conversation(
    device_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation history for a device."""
    import json as _json
    result = await db.execute(
        select(Device).where(Device.device_id == device_id, Device.user_id == user.id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        messages = _json.loads(device.conversation_json) if device.conversation_json else []
    except Exception:
        messages = []
    return {"device_id": device_id, "messages": messages}


@router.delete("/devices/{device_id}/conversation")
async def clear_conversation(
    device_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear conversation history for a device."""
    result = await db.execute(
        select(Device).where(Device.device_id == device_id, Device.user_id == user.id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device.conversation_json = "[]"
    await db.commit()
    return {"ok": True}


# ── Usage Statistics ────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get usage statistics for the current user."""
    from sqlalchemy import func

    # Device count
    dev_result = await db.execute(
        select(func.count()).select_from(Device).where(Device.user_id == user.id)
    )
    device_count = dev_result.scalar() or 0

    # Meeting stats
    mtg_result = await db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Meeting.duration_s), 0),
        ).where(Meeting.user_id == user.id)
    )
    mtg_row = mtg_result.one()
    meeting_count = mtg_row[0] or 0
    meeting_total_s = mtg_row[1] or 0

    # Reminder stats
    rem_result = await db.execute(
        select(func.count()).select_from(Reminder).where(Reminder.user_id == user.id)
    )
    reminder_count = rem_result.scalar() or 0

    rem_delivered = await db.execute(
        select(func.count()).select_from(Reminder).where(
            Reminder.user_id == user.id, Reminder.delivered == 1
        )
    )
    reminder_delivered = rem_delivered.scalar() or 0

    # Conversation message counts per device
    devices_result = await db.execute(
        select(Device.device_id, Device.name, Device.conversation_json, Device.last_seen)
        .where(Device.user_id == user.id)
    )
    import json as _json
    device_stats = []
    total_messages = 0
    for row in devices_result:
        try:
            conv = _json.loads(row.conversation_json) if row.conversation_json else []
        except Exception:
            conv = []
        msg_count = len(conv)
        total_messages += msg_count
        device_stats.append({
            "device_id": row.device_id,
            "name": row.name or row.device_id,
            "message_count": msg_count,
            "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        })

    return {
        "devices": device_count,
        "meetings": meeting_count,
        "meeting_duration_s": meeting_total_s,
        "reminders": reminder_count,
        "reminders_delivered": reminder_delivered,
        "total_messages": total_messages,
        "device_stats": device_stats,
    }
