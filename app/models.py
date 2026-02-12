"""SQLAlchemy ORM models for users, devices, and settings."""
import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    devices = relationship("Device", back_populates="owner", cascade="all, delete-orphan")
    settings = relationship("UserSettings", back_populates="owner", uselist=False, cascade="all, delete-orphan")


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), unique=True, nullable=False, index=True)
    token_hash = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # nullable for legacy/unbound
    name = Column(String(128), default="")
    fw_version = Column(String(32), default="")
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="devices")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # LLM settings (OpenAI-compatible: OpenAI, DeepSeek, Groq, OpenRouter, Ollama)
    openai_api_key_enc = Column(Text, default="")
    openai_base_url = Column(String(512), default="")
    openai_chat_model = Column(String(64), default="")
    openai_asr_model = Column(String(64), default="")

    # TTS settings (provider: "openai" or "edge")
    tts_provider = Column(String(32), default="")  # "" = openai (default)
    openai_tts_model = Column(String(64), default="")
    openai_tts_voice = Column(String(32), default="")

    # Tool API keys (encrypted at rest)
    weather_api_key_enc = Column(Text, default="")
    weather_city = Column(String(64), default="")
    tavily_api_key_enc = Column(Text, default="")
    youtube_api_key_enc = Column(Text, default="")

    # Notion integration
    notion_token_enc = Column(Text, default="")
    notion_database_id = Column(String(64), default="")

    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="settings")


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    device_id = Column(String(64), nullable=False)
    session_id = Column(String(8), unique=True, nullable=False)
    title = Column(String(256), default="会议")
    audio_path = Column(String(512), default="")
    duration_s = Column(Integer, default=0)
    transcript = Column(Text, default="")
    status = Column(String(16), default="recording")  # recording / ended / transcribed
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    owner = relationship("User")


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    device_id = Column(String(64), nullable=False)
    remind_at = Column(DateTime, nullable=False)
    message = Column(Text, nullable=False)
    delivered = Column(Integer, default=0)  # 0=pending, 1=delivered, 2=failed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    owner = relationship("User")
