"""Meeting recording tools — start/stop/transcribe meetings."""
import uuid
import logging

from ..registry import register_tool, ToolResult, ToolParam

logger = logging.getLogger(__name__)


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
        return ToolResult(type="tts", text="录音时间太短，未保存")

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

    # Speak a summary (first 200 chars), full transcript in data
    summary = transcript[:200] + ("..." if len(transcript) > 200 else "")
    logger.info(f"Meeting transcript ({len(transcript)} chars): {transcript[:100]}...")
    return ToolResult(type="tts", text=f"会议内容：{summary}", data={"transcript": transcript})
