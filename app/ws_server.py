"""WebSocket server — thin connection lifecycle + message routing.

All pipeline logic (ASR/LLM/TTS/rate-control) lives in pipeline.py.
Session state lives in session.py.
"""
import asyncio
import json
import logging
import traceback
from datetime import datetime

import websockets
from websockets.server import WebSocketServerProtocol
from sqlalchemy import select

from .config import settings
from .session import Session, UserConfig
from .pipeline import run_pipeline, ws_send_safe
from .registry import registry
from .llm import reset_conversation, load_conversation, get_conversation
from .database import async_session_factory
from .models import Device, UserSettings
from .auth import verify_token, decrypt_secret

logger = logging.getLogger(__name__)

# Active device connections: device_id → (ws, session)
# Used by reminder scheduler to push TTS to online devices
_active_connections: dict[str, tuple[WebSocketServerProtocol, Session]] = {}


def get_active_connection(device_id: str):
    """Get active WS connection for a device. Returns (ws, session) or None."""
    entry = _active_connections.get(device_id)
    if entry and not entry[0].closed:
        return entry
    return None


def get_all_active_devices() -> list[str]:
    """Get list of currently connected device IDs."""
    return [did for did, (ws, _) in _active_connections.items() if not ws.closed]


async def handle_text_message(ws: WebSocketServerProtocol, session: Session, text: str):
    """Route incoming JSON messages."""
    try:
        payload = json.loads(text)
    except Exception:
        await ws_send_safe(ws, json.dumps({"type": "error", "message": "invalid json"}), session)
        return

    mtype = payload.get("type")
    session.touch()
    logger.info(f"[{session.session_id}] Device {session.device_id}: {mtype}")

    if mtype == "hello":
        listen_mode = payload.get("listen_mode")
        if listen_mode:
            session.listen_mode = listen_mode
            session.protocol_version = 2
            logger.info(f"[{session.session_id}] Xiaozhi protocol v2, listen_mode={listen_mode}")

        # Track firmware version
        fw_version = payload.get("fw", "")
        if fw_version:
            session.fw_version = fw_version
            logger.info(f"[{session.session_id}] Device firmware: v{fw_version}")
            # Update DB
            try:
                async with async_session_factory() as db:
                    result = await db.execute(select(Device).where(Device.device_id == session.device_id))
                    dev = result.scalar_one_or_none()
                    if dev:
                        dev.fw_version = fw_version
                        await db.commit()
            except Exception as e:
                logger.warning(f"Failed to update fw_version in DB: {e}")

        hello_resp = {
            "type": "hello",
            "session_id": session.session_id,
            "audio_params": {
                "sample_rate": settings.pcm_sample_rate,
                "channels": settings.pcm_channels,
                "codec": "opus",
                "frame_duration_ms": settings.frame_duration_ms,
            },
            "features": {"asr": True, "tts": True, "llm": True, "abort": True},
            "version": session.protocol_version,
        }
        await ws_send_safe(ws, json.dumps(hello_resp), session, "hello_resp")
        logger.info(f"[{session.session_id}] Hello handshake complete")

    elif mtype == "audio_start":
        session.opus_packets = []
        session.listening = True
        session.tts_abort = False

    elif mtype == "audio_end":
        session.listening = False
        _launch_pipeline(ws, session)

    elif mtype == "listen":
        listen_state = payload.get("state")
        listen_mode = payload.get("mode")

        if listen_state == "detect":
            logger.info(f"[{session.session_id}] Wake detected: text={payload.get('text')}")

        elif listen_state == "start":
            if listen_mode:
                session.listen_mode = listen_mode
            session.opus_packets = []
            session.listening = True
            session.tts_abort = False
            logger.info(f"[{session.session_id}] Listen start (mode={listen_mode})")

        elif listen_state == "stop":
            session.listening = False
            logger.info(f"[{session.session_id}] Listen stop, launching pipeline...")
            _launch_pipeline(ws, session)

    elif mtype == "abort":
        reason = payload.get("reason", "unknown")
        logger.info(f"[{session.session_id}] Abort requested (reason={reason})")
        if session.music_playing and reason == "wake_word_detected":
            # Pause music (not stop) — user might want to resume after interaction
            session._music_pause_event.clear()
            session.music_paused = True
            logger.info(f"[{session.session_id}] Music paused for voice interaction")
        else:
            session.tts_abort = True
            await ws_send_safe(ws, json.dumps({"type": "tts_end", "reason": "abort"}), session, "abort_ack")

    elif mtype == "music_ctrl":
        action = payload.get("action")
        if action == "pause":
            session._music_pause_event.clear()
            session.music_paused = True
            logger.info(f"[{session.session_id}] Music paused by device")
        elif action == "resume":
            session._music_pause_event.set()
            session.music_paused = False
            logger.info(f"[{session.session_id}] Music resume requested by device")
        elif action == "stop":
            session.music_abort = True
            session._music_pause_event.set()  # Unblock if paused
            logger.info(f"[{session.session_id}] Music stopped by device")

    elif mtype == "ping":
        await ws_send_safe(ws, json.dumps({"type": "pong"}), session, "pong")


def _launch_pipeline(ws: WebSocketServerProtocol, session: Session):
    """Launch the ASR→LLM→TTS pipeline as a background task."""
    if session.processing:
        logger.warning(f"[{session.session_id}] Already processing, ignoring new request")
        return

    if session._process_task and not session._process_task.done():
        logger.warning(f"[{session.session_id}] Cancelling previous pipeline task")
        session._process_task.cancel()

    session._process_task = asyncio.create_task(_pipeline_wrapper(ws, session))


async def _pipeline_wrapper(ws: WebSocketServerProtocol, session: Session):
    """Wrapper to catch unhandled exceptions from the pipeline."""
    try:
        await run_pipeline(ws, session)
    except asyncio.CancelledError:
        logger.info(f"[{session.session_id}] Pipeline cancelled")
        session.processing = False
    except Exception as e:
        logger.error(f"[{session.session_id}] UNHANDLED in pipeline: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        session.processing = False
        try:
            await ws_send_safe(ws, json.dumps({"type": "error", "message": f"Internal error: {e}"}), session)
        except Exception:
            pass


async def _load_user_config(device_id: str, token: str) -> UserConfig | None:
    """Query DB for device → user → settings. Returns UserConfig or None."""
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(Device).where(Device.device_id == device_id))
            device = result.scalar_one_or_none()
            if not device:
                return None
            if not verify_token(token, device.token_hash):
                return None

            # Update last_seen
            device.last_seen = datetime.utcnow()
            await db.commit()

            if not device.user_id:
                # Device exists but unbound — auth OK, no per-user config
                return UserConfig()

            # Load user settings
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == device.user_id)
            )
            us = result.scalar_one_or_none()
            if not us:
                return UserConfig(user_id=device.user_id)

            return UserConfig(
                user_id=device.user_id,
                openai_api_key=decrypt_secret(us.openai_api_key_enc) if us.openai_api_key_enc else "",
                openai_base_url=us.openai_base_url or "",
                openai_chat_model=us.openai_chat_model or "",
                openai_asr_model=us.openai_asr_model or "",
                tts_provider=us.tts_provider or "",
                openai_tts_model=us.openai_tts_model or "",
                openai_tts_voice=us.openai_tts_voice or "",
                weather_api_key=decrypt_secret(us.weather_api_key_enc) if us.weather_api_key_enc else "",
                weather_city=us.weather_city or "",
                tavily_api_key=decrypt_secret(us.tavily_api_key_enc) if us.tavily_api_key_enc else "",
                youtube_api_key=decrypt_secret(us.youtube_api_key_enc) if us.youtube_api_key_enc else "",
                notion_token=decrypt_secret(us.notion_token_enc) if us.notion_token_enc else "",
                notion_database_id=us.notion_database_id or "",
            )
    except Exception as e:
        logger.error(f"DB auth error for {device_id}: {e}")
        return None


async def handle_client(ws: WebSocketServerProtocol, path: str):
    """Main WebSocket connection handler — auth, message loop, cleanup."""
    device_id = ws.request_headers.get("x-device-id")
    token = ws.request_headers.get("x-device-token")

    logger.info(f"New connection from {ws.remote_address}, path: {path}")

    if not device_id or not token:
        logger.warning(f"Missing credentials from {ws.remote_address}")
        await ws_send_safe(ws, json.dumps({"type": "error", "message": "missing device_id/token"}), Session("unknown"))
        await ws.close(code=4401, reason="missing credentials")
        return

    # Try DB auth first, fallback to legacy registry
    user_config = await _load_user_config(device_id, token)
    if user_config is None and not registry.is_valid(device_id, token):
        logger.warning(f"Invalid token for device {device_id}")
        await ws_send_safe(ws, json.dumps({"type": "error", "message": "invalid token"}), Session("unknown"))
        await ws.close(code=4401, reason="invalid token")
        return

    session = Session(device_id)
    if user_config:
        session.config = user_config
        logger.info(f"[{session.session_id}] Device {device_id} authenticated via DB (user_id={user_config.user_id})")
    else:
        logger.info(f"[{session.session_id}] Device {device_id} authenticated via legacy registry")

    # Register active connection for server-push (reminders, etc.)
    _active_connections[device_id] = (ws, session)

    # Load persistent conversation history from DB
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(Device).where(Device.device_id == device_id))
            device_record = result.scalar_one_or_none()
            if device_record and device_record.conversation_json:
                conv = json.loads(device_record.conversation_json)
                if isinstance(conv, list) and conv:
                    load_conversation(device_id, conv)
    except Exception as e:
        logger.warning(f"[{session.session_id}] Failed to load conversation history: {e}")

    try:
        async for message in ws:
            if isinstance(message, str):
                await handle_text_message(ws, session, message)
            elif isinstance(message, bytes):
                # Accumulate Opus audio packets
                if session.listening:
                    session.opus_packets.append(bytes(message))
                    session.touch()
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"[{session.session_id}] Device {device_id} disconnected")
    except Exception as e:
        logger.error(f"[{session.session_id}] Error handling device {device_id}: {e}", exc_info=True)
    finally:
        _active_connections.pop(device_id, None)
        session.tts_abort = True
        session.music_abort = True
        session._music_pause_event.set()  # Unblock music if paused
        if session._process_task and not session._process_task.done():
            logger.info(f"[{session.session_id}] Waiting for pipeline to finish...")
            try:
                await asyncio.wait_for(session._process_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                session._process_task.cancel()
                logger.warning(f"[{session.session_id}] Force-cancelled pipeline")
        # Auto-save meeting recording if still active
        if session.meeting_active and session._meeting_audio_buffer:
            session.meeting_active = False
            try:
                from .tools.builtin.meeting import _save_meeting_audio, _update_meeting_record
                audio_path = _save_meeting_audio(session.meeting_session_id, session._meeting_audio_buffer)
                if session.meeting_db_id:
                    from datetime import datetime
                    duration_s = len(session._meeting_audio_buffer) / 2 / 16000
                    await _update_meeting_record(
                        session.meeting_db_id,
                        status="ended",
                        duration_s=int(duration_s),
                        audio_path=audio_path,
                        ended_at=datetime.utcnow(),
                    )
                logger.info(f"[{session.session_id}] Meeting auto-saved on disconnect: {session.meeting_session_id}")
            except Exception as e:
                logger.error(f"[{session.session_id}] Failed to auto-save meeting: {e}")

        # Save conversation history to DB (persist across reconnects)
        try:
            conv = get_conversation(device_id)
            async with async_session_factory() as db:
                result = await db.execute(select(Device).where(Device.device_id == device_id))
                device_record = result.scalar_one_or_none()
                if device_record:
                    device_record.conversation_json = json.dumps(conv[-20:], ensure_ascii=False)
                    await db.commit()
        except Exception as e:
            logger.warning(f"[{session.session_id}] Failed to save conversation history: {e}")
        # Clean up in-memory (will be reloaded on next connect)
        reset_conversation(device_id)
        logger.info(f"[{session.session_id}] Session ended for device {device_id}")


async def start_websocket_server():
    """Start the WebSocket server."""
    logger.info(f"Starting WebSocket server on {settings.ws_host}:{settings.ws_port}")

    async with websockets.serve(
        handle_client,
        settings.ws_host,
        settings.ws_port,
        ping_interval=None,
        ping_timeout=None,
        write_limit=65536,   # 64KB — avoids WS backpressure during music streaming
        max_queue=64,
    ):
        logger.info(f"WebSocket server listening on ws://{settings.ws_host}:{settings.ws_port}/ws")
        await asyncio.Future()
