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

    # Add missing columns for schema upgrades (SQLite ALTER TABLE)
    await _add_column_if_missing(engine, "user_settings", "tts_provider", "VARCHAR(32) DEFAULT ''")
    await _add_column_if_missing(engine, "user_settings", "weather_api_key_enc", "TEXT DEFAULT ''")
    await _add_column_if_missing(engine, "user_settings", "weather_city", "VARCHAR(64) DEFAULT ''")
    await _add_column_if_missing(engine, "user_settings", "tavily_api_key_enc", "TEXT DEFAULT ''")
    await _add_column_if_missing(engine, "user_settings", "youtube_api_key_enc", "TEXT DEFAULT ''")
    await _add_column_if_missing(engine, "user_settings", "notion_token_enc", "TEXT DEFAULT ''")
    await _add_column_if_missing(engine, "user_settings", "notion_database_id", "VARCHAR(64) DEFAULT ''")
    await _add_column_if_missing(engine, "devices", "conversation_json", "TEXT DEFAULT '[]'")

    logger.info(f"Database initialized at {DB_PATH}")
    await _migrate_legacy_devices()


async def _add_column_if_missing(engine, table: str, column: str, col_type: str):
    """Add a column to an existing table if it doesn't exist (SQLite schema migration)."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            logger.info(f"Migration: added column {table}.{column}")
        except Exception:
            pass  # Column already exists


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
