"""Meeting recording tools — start/stop/transcribe with persistent storage."""
import os
import uuid
import logging
from datetime import datetime

from ..registry import register_tool, ToolResult, ToolParam
from ...meeting_notifications import notify_meeting_status

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


async def _generate_meeting_summary(transcript: str, session) -> str:
    """Generate AI summary of meeting transcript using LLM (Pro mode compatible)."""
    from openai import AsyncOpenAI
    from ...config import settings

    # Use user's OpenAI config if available (Pro mode)
    if session and session.config.openai_base_url and session.config.openai_api_key:
        client = AsyncOpenAI(
            api_key=session.config.openai_api_key,
            base_url=session.config.openai_base_url,
        )
        model = session.config.openai_chat_model or "gpt-4"
        logger.info(f"Using Pro mode LLM: {session.config.openai_base_url}")
    else:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        model = settings.openai_chat_model or "gpt-4-turbo"
        logger.info(f"Using default LLM: {model}")

    prompt = f"""请总结以下会议内容，提取关键信息：

# 会议转录
{transcript}

# 输出格式
请按以下格式输出：

## 会议主题
[简短描述会议主题]

## 关键要点
- [要点1]
- [要点2]
- [要点3]

## 决策事项
- [决策1]
- [决策2]

## 行动项
- [行动1] - [负责人/时间]
- [行动2] - [负责人/时间]

注意：
- 使用简洁的中文
- 如果某个部分没有内容，可以省略
- 突出重点，避免冗长
"""

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个专业的会议记录助手，擅长提炼会议要点。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=1000,
        )
        summary = response.choices[0].message.content.strip()
        return summary
    except Exception as e:
        logger.error(f"LLM summary failed: {e}")
        return ""


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

    # 发送录音状态通知到设备
    await notify_meeting_status(session, "recording", session_id=session.meeting_session_id)

    return ToolResult(type="tts", text=f"开始录制{display_title}，屏幕会显示录音状态。说\"结束会议\"来停止。")


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

        # 通知结束
        await notify_meeting_status(session, "ended")

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

    # 通知结束
    await notify_meeting_status(session, "ended", duration_s=int(duration_s))

    return ToolResult(
        type="tts",
        text=f"会议录音已结束，共{int(duration_s)}秒。说\"转录\"可以获取转录和总结。",
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

    # 通知开始转录
    await notify_meeting_status(session, "transcribing")

    # Chunk into 25s segments for Whisper
    chunk_size = 25 * 16000 * 2  # 25s * 16kHz * 2 bytes
    chunks = [bytes(audio_buffer[i:i + chunk_size]) for i in range(0, len(audio_buffer), chunk_size)]

    logger.info(f"Meeting transcribe: {len(audio_buffer)} bytes -> {len(chunks)} chunks")

    # 并行转录（最多5个并发），保持顺序
    import asyncio
    sem = asyncio.Semaphore(5)

    async def _transcribe_chunk(i: int, chunk: bytes) -> str:
        async with sem:
            text = await transcribe_pcm(chunk, session=session)
            logger.info(f"Transcribe chunk {i+1}/{len(chunks)}: '{text[:50]}...' " if text else f"Transcribe chunk {i+1}/{len(chunks)}: empty")
            return text or ""

    results = await asyncio.gather(*[_transcribe_chunk(i, c) for i, c in enumerate(chunks)])
    full_text = [t for t in results if t]

    transcript = " ".join(full_text)
    if not transcript:
        await notify_meeting_status(session, "completed", success=False)
        return ToolResult(type="tts", text="录音转写为空，可能没有清晰的语音内容")

    # Generate AI summary
    summary_text = ""
    if len(transcript) > 100:  # Only summarize if transcript is substantial
        try:
            summary_text = await _generate_meeting_summary(transcript, session)
            logger.info(f"Meeting summary generated: {len(summary_text)} chars")
        except Exception as e:
            logger.error(f"Failed to generate meeting summary: {e}")

    # Save transcript + summary to DB
    if session.meeting_db_id:
        try:
            # Store summary in a custom field (or append to transcript)
            full_content = f"{transcript}\n\n--- 会议总结 ---\n{summary_text}" if summary_text else transcript
            await _update_meeting_record(session.meeting_db_id, transcript=full_content, status="transcribed")
        except Exception as e:
            logger.error(f"Failed to save transcript to DB: {e}")

    # Auto-push to Notion if configured (auto-creates database if needed)
    notion_pushed = False
    notion_url = ""
    if session and session.config.notion_token:
        try:
            from .notion import push_meeting_to_notion, _get_or_create_database

            # Get or create default database
            token, database_id = await _get_or_create_database(session)
            if token and database_id:
                result = await push_meeting_to_notion(
                    token=token,
                    database_id=database_id,
                    title=session.meeting_session_id or "会议",
                    transcript=transcript,
                    summary=summary_text,  # 传递总结
                    duration_s=int(len(audio_buffer) / 2 / 16000),
                )
                if result:
                    notion_pushed = True
                    notion_url = result.get("url", "")
            else:
                logger.warning("Notion token configured but failed to get/create database")
        except Exception as e:
            logger.error(f"Failed to push meeting to Notion: {e}")

    # 通知转录完成 - 改为静默返回
    await notify_meeting_status(
        session,
        "completed",
        success=True,
        notion_pushed=notion_pushed,
        notion_url=notion_url,
        transcript_length=len(transcript),
        has_summary=bool(summary_text)
    )

    # 改为静默返回，只给简短通知
    if notion_pushed:
        notification_text = "会议已转录完成并保存到Notion"
    else:
        notification_text = "会议已转录完成"

    logger.info(f"Meeting transcript ({len(transcript)} chars) + summary ({len(summary_text)} chars)")

    # 返回类型改为 "silent"，不播报
    return ToolResult(
        type="silent",  # 原来是 "tts"
        text=notification_text,
        data={
            "transcript": transcript,
            "summary": summary_text,
            "notion_pushed": notion_pushed,
            "notion_url": notion_url
        }
    )


def _extract_voice_summary(summary_text: str) -> str:
    """Extract concise voice-friendly summary from full summary text."""
    # Try to extract just the key points section
    lines = summary_text.split('\n')
    key_points = []
    in_key_section = False

    for line in lines:
        line = line.strip()
        if '关键要点' in line or '主要内容' in line:
            in_key_section = True
            continue
        if in_key_section and line.startswith('-'):
            key_points.append(line[1:].strip())
        elif in_key_section and line.startswith('##'):
            break  # Next section

    if key_points:
        points_text = "、".join(key_points[:3])  # Max 3 points
        return f"主要内容包括：{points_text}。"
    else:
        # Fallback: just say it's summarized
        return "已生成会议总结。"
