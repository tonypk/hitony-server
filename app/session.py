"""Session state management for WebSocket connections."""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class UserConfig:
    """Per-user API configuration, loaded at WebSocket connect time.
    Empty strings mean 'use global default from settings'.
    """
    user_id: int = 0

    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_chat_model: str = ""
    openai_asr_model: str = ""

    tts_provider: str = ""  # "" or "openai" = OpenAI TTS, "edge" = Edge TTS
    openai_tts_model: str = ""
    openai_tts_voice: str = ""

    weather_api_key: str = ""
    weather_city: str = ""
    tavily_api_key: str = ""

    def get(self, field_name: str, fallback: str) -> str:
        """Return user value if set, otherwise fallback to global default."""
        val = getattr(self, field_name, "")
        return val if val else fallback


class Session:
    """Per-connection session state, extracted from ws_server.ConnState."""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.session_id = str(uuid.uuid4())[:8]
        self.opus_packets: List[bytes] = []
        self.listening = False
        self.tts_abort = False
        self.processing = False
        self.listen_mode: Optional[str] = None
        self.protocol_version: int = 1
        self._process_task: Optional[asyncio.Task] = None

        # Activity tracking (xiaozhi pattern)
        now = time.monotonic()
        self.first_activity_time = now
        self.last_activity_time = now

        # Music state
        self.music_playing: bool = False
        self.music_paused: bool = False
        self.music_abort: bool = False
        self.music_title: str = ""
        self._music_task: Optional[asyncio.Task] = None
        self._music_pause_event: asyncio.Event = asyncio.Event()
        self._music_pause_event.set()  # Start unpaused

        # Per-user config (populated during WS auth)
        self.config: UserConfig = UserConfig()

        # Tool system: pending follow-up for ask_user flow
        self._pending_tool_call: Optional[dict] = None

        # Meeting recording state
        self.meeting_active: bool = False
        self.meeting_session_id: Optional[str] = None
        self._meeting_audio_buffer: bytearray = bytearray()

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity_time = time.monotonic()

    def idle_seconds(self) -> float:
        """Seconds since last activity."""
        return time.monotonic() - self.last_activity_time
