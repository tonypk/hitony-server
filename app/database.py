"""Async SQLite database engine, session factory, and initialization."""
import json
import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hitony.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency: yield an async DB session."""
    async with async_session_factory() as session:
        yield session


async def init_db():
    """Create all tables and migrate legacy data."""
    os.makedirs(DB_PATH.parent, exist_ok=True)

    async with engine.begin() as conn:
        from . import models  # noqa: ensure models are registered
        await conn.run_sync(Base.metadata.create_all)

    logger.info(f"Database initialized at {DB_PATH}")
    await _migrate_legacy_devices()


async def _migrate_legacy_devices():
    """One-time migration: import devices.json into the DB if present."""
    devices_json = Path(__file__).resolve().parent.parent / "data" / "devices.json"
    if not devices_json.exists():
        return

    from .models import Device
    from .auth import hash_token

    async with async_session_factory() as db:
        with open(devices_json) as f:
            legacy = json.load(f)

        migrated = 0
        for device_id, token in legacy.items():
            result = await db.execute(select(Device).where(Device.device_id == device_id))
            if result.scalar_one_or_none():
                continue  # already migrated
            device = Device(
                device_id=device_id,
                token_hash=hash_token(token),
                user_id=None,  # no owner yet
            )
            db.add(device)
            migrated += 1

        if migrated:
            await db.commit()
            logger.info(f"Migrated {migrated} legacy devices from devices.json")
