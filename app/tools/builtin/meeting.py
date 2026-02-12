"""Meeting recording tools — start/stop/transcribe with persistent storage."""
import os
import uuid
import logging
from datetime import datetime

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)

# Meeting audio storage directory
MEETINGS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "meetings")


def _save_meeting_audio(session_id: str, audio_buffer: bytearray, user_id: int = 0) -> str:
    """Save PCM audio buffer as WAV file. Returns relative path.

    Files are organized by user: data/meetings/user_{id}/{session_id}.wav
    Unbound devices go to data/meetings/unbound/
    """
    from ...asr import pcm_to_wav

    user_dir = f"user_{user_id}" if user_id else "unbound"
    save_dir = os.path.join(MEETINGS_DIR, user_dir)
    os.makedirs(save_dir, exist_ok=True)

    filename = f"{session_id}.wav"
    filepath = os.path.join(save_dir, filename)

    wav_bytes = pcm_to_wav(bytes(audio_buffer))
    with open(filepath, "wb") as f:
        f.write(wav_bytes)

    rel_path = f"meetings/{user_dir}/{filename}"
    logger.info(f"Meeting audio saved: {filepath} ({len(audio_buffer)} bytes PCM -> {len(wav_bytes)} bytes WAV)")
    return rel_path


async def _create_meeting_record(session, title: str) -> int:
    """Create a Meeting DB record. Returns meeting.id."""
    from ...database import async_session_factory
    from ...models import Meeting

    async with async_session_factory() as db:
        meeting = Meeting(
            user_id=session.config.user_id if session.config.user_id else None,
            device_id=session.device_id,
            session_id=session.meeting_session_id,
            title=title,
            status="recording",
            started_at=datetime.utcnow(),
        )
        db.add(meeting)
        await db.commit()
        await db.refresh(meeting)
        logger.info(f"Meeting DB record created: id={meeting.id}, session={session.meeting_session_id}")
        return meeting.id


async def _update_meeting_record(db_id: int, **kwargs):
    """Update a Meeting DB record by id."""
    from ...database import async_session_factory
    from ...models import Meeting
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(select(Meeting).where(Meeting.id == db_id))
        meeting = result.scalar_one_or_none()
        if not meeting:
            logger.warning(f"Meeting record not found: id={db_id}")
            return
        for k, v in kwargs.items():
            setattr(meeting, k, v)
        await db.commit()
        logger.info(f"Meeting DB updated: id={db_id}, fields={list(kwargs.keys())}")


@register_tool(
    "meeting.start",
    description="Start recording a meeting",
    params=[ToolParam("title", description="meeting title", required=False, default="")],
    category="meeting",
)
async def meeting_start(title: str = "", session=None, **kwargs) -> ToolResult:
    if session.meeting_active:
        return ToolResult(type="tts", text="已经在录音中了")

    session.meeting_active = True
    session.meeting_session_id = str(uuid.uuid4())[:8]
    session._meeting_audio_buffer = bytearray()

    display_title = title or "会议"

    # Create DB record
    try:
        session.meeting_db_id = await _create_meeting_record(session, display_title)
    except Exception as e:
        logger.error(f"Failed to create meeting DB record: {e}")

    logger.info(f"Meeting started: {session.meeting_session_id} - {display_title}")
    return ToolResult(type="tts", text=f"开始录制{display_title}，每次对话的语音都会被记录。说\"结束会议\"来停止。")


@register_tool(
    "meeting.end",
    description="End the current meeting recording",
    category="meeting",
)
async def meeting_end(session=None, **kwargs) -> ToolResult:
    if not session.meeting_active:
        return ToolResult(type="tts", text="当前没有在录音")

    session.meeting_active = False
    duration_s = len(session._meeting_audio_buffer) / 2 / 16000
    meeting_id = session.meeting_session_id

    logger.info(f"Meeting ended: {meeting_id}, {duration_s:.0f}s audio")

    if duration_s < 1.0:
        session._meeting_audio_buffer = bytearray()
        if session.meeting_db_id:
            await _update_meeting_record(session.meeting_db_id, status="ended", duration_s=0, ended_at=datetime.utcnow())
        return ToolResult(type="tts", text="录音时间太短，未保存")

    # Save audio to file
    try:
        user_id = session.config.user_id if session.config.user_id else 0
        audio_path = _save_meeting_audio(meeting_id, session._meeting_audio_buffer, user_id=user_id)
    except Exception as e:
        logger.error(f"Failed to save meeting audio: {e}")
        audio_path = ""

    # Update DB record
    if session.meeting_db_id:
        try:
            await _update_meeting_record(
                session.meeting_db_id,
                status="ended",
                duration_s=int(duration_s),
                audio_path=audio_path,
                ended_at=datetime.utcnow(),
            )
        except Exception as e:
            logger.error(f"Failed to update meeting DB: {e}")

    return ToolResult(
        type="tts",
        text=f"会议录音已结束，共{int(duration_s)}秒。说\"转录\"可以获取文字内容。",
        data={"meeting_id": meeting_id, "duration_s": duration_s},
    )


@register_tool(
    "meeting.transcribe",
    description="Transcribe the recorded meeting audio to text",
    long_running=True,
    category="meeting",
)
async def meeting_transcribe(session=None, **kwargs) -> ToolResult:
    audio_buffer = session._meeting_audio_buffer

    # If buffer is empty (e.g. reconnected), try loading from file
    if not audio_buffer and session.meeting_session_id:
        user_id = session.config.user_id if session.config.user_id else 0
        user_dir = f"user_{user_id}" if user_id else "unbound"
        wav_path = os.path.join(MEETINGS_DIR, user_dir, f"{session.meeting_session_id}.wav")
        if os.path.exists(wav_path):
            logger.info(f"Loading meeting audio from file: {wav_path}")
            with open(wav_path, "rb") as f:
                wav_data = f.read()
            # Strip WAV header (44 bytes) to get raw PCM
            audio_buffer = wav_data[44:]

    if not audio_buffer:
        return ToolResult(type="tts", text="没有可转录的录音")

    from ...asr import transcribe_pcm

    # Chunk into 25s segments for Whisper
    chunk_size = 25 * 16000 * 2  # 25s * 16kHz * 2 bytes
    chunks = [bytes(audio_buffer[i:i + chunk_size]) for i in range(0, len(audio_buffer), chunk_size)]

    logger.info(f"Meeting transcribe: {len(audio_buffer)} bytes -> {len(chunks)} chunks")

    full_text = []
    for i, chunk in enumerate(chunks):
        text = await transcribe_pcm(chunk, session=session)
        if text:
            full_text.append(text)
        logger.info(f"Transcribe chunk {i+1}/{len(chunks)}: '{text[:50]}...' " if text else f"Transcribe chunk {i+1}/{len(chunks)}: empty")

    transcript = " ".join(full_text)
    if not transcript:
        return ToolResult(type="tts", text="录音转写为空，可能没有清晰的语音内容")

    # Save transcript to DB
    if session.meeting_db_id:
        try:
            await _update_meeting_record(session.meeting_db_id, transcript=transcript, status="transcribed")
        except Exception as e:
            logger.error(f"Failed to save transcript to DB: {e}")

    # Auto-push to Notion if configured
    notion_pushed = False
    if session and session.config.notion_token and session.config.notion_database_id:
        try:
            from .notion import push_meeting_to_notion
            notion_pushed = await push_meeting_to_notion(
                token=session.config.notion_token,
                database_id=session.config.notion_database_id,
                title=session.meeting_session_id or "会议",
                transcript=transcript,
                duration_s=int(len(audio_buffer) / 2 / 16000),
            )
        except Exception as e:
            logger.error(f"Failed to push meeting to Notion: {e}")

    # Speak a summary (first 200 chars), full transcript in data
    summary = transcript[:200] + ("..." if len(transcript) > 200 else "")
    notion_msg = "，已同步到Notion" if notion_pushed else ""
    logger.info(f"Meeting transcript ({len(transcript)} chars): {transcript[:100]}...")
    return ToolResult(type="tts", text=f"会议内容：{summary}{notion_msg}", data={"transcript": transcript})
