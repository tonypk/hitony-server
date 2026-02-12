"""REST API routes: auth, devices, user settings."""
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import User, Device, UserSettings, Reminder
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

class SettingsOut(BaseModel):
    openai_api_key_set: bool
    openai_base_url: str
    openai_chat_model: str
    openai_asr_model: str
    tts_provider: str
    openai_tts_model: str
    openai_tts_voice: str


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
    await db.commit()
    logger.info(f"Settings updated for user {user.email}")
    return {"ok": True}


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
