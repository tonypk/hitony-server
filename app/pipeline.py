"""ASR → Router/LLM → Tool → TTS pipeline with batched audio streaming.

Batching reduces TCP segments through phone hotspot: instead of 117 individual
WebSocket messages (one per Opus packet), we send ~12 batched messages (10 packets
each). Each batch is ~2KB — fits in one TCP segment and one ESP32 WS buffer read.
"""
import asyncio
import json
import logging
import struct
import time
from typing import Optional, List

import opuslib
from websockets.server import WebSocketServerProtocol

from .config import settings
from .session import Session
from .asr import transcribe_pcm
from .tts import synthesize_tts
from .llm import plan_intent, append_user_message, append_assistant_message
from .tools import route_intent, execute_tool, get_tool

logger = logging.getLogger(__name__)

WS_SEND_TIMEOUT = 2.0
MUSIC_WS_SEND_TIMEOUT = 8.0  # Music batches need longer timeout on slow links


async def ws_send_safe(ws: WebSocketServerProtocol, data, session: Session,
                       label: str = "", timeout: float = 0) -> bool:
    """Send data via WebSocket with timeout. Returns True on success."""
    t = timeout if timeout > 0 else WS_SEND_TIMEOUT
    try:
        await asyncio.wait_for(ws.send(data), timeout=t)
        return True
    except asyncio.TimeoutError:
        logger.error(f"[{session.session_id}] ws.send() timed out ({t}s) {label}")
        return False
    except Exception as e:
        logger.warning(f"[{session.session_id}] ws.send() failed {label}: {type(e).__name__}: {e}")
        return False


async def run_pipeline(ws: WebSocketServerProtocol, session: Session):
    """Full pipeline: decode Opus → ASR → Router/LLM → Tool → TTS → send."""
    if not session.opus_packets:
        await ws_send_safe(ws, json.dumps({"type": "error", "message": "empty audio"}), session)
        return

    session.processing = True
    pipeline_t0 = time.monotonic()

    try:
        keepalive_task = asyncio.create_task(_keepalive_pings(ws, session))

        try:
            asr_result = await _decode_and_asr(ws, session)
            if asr_result is None:
                return

            await _process_and_speak(ws, session, asr_result)
        finally:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass

    finally:
        session.processing = False
        elapsed = time.monotonic() - pipeline_t0
        logger.info(f"[{session.session_id}] Pipeline total: {elapsed:.1f}s")


async def _keepalive_pings(ws: WebSocketServerProtocol, session: Session):
    """Send WS pings every 1s during processing to keep TCP cwnd open."""
    try:
        while True:
            await asyncio.sleep(1.0)
            if ws.closed or session.tts_abort:
                break
            try:
                await ws.ping()
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _send_tts_round(ws, session, opus_packets, text):
    """Send a complete TTS round: tts_start → batched audio → tts_end."""
    sid = session.session_id

    ok = await ws_send_safe(ws, json.dumps({"type": "tts_start", "text": text}), session, "tts_start")
    if not ok:
        logger.error(f"[{sid}] Failed to send tts_start")
        return 0

    sent = await _stream_batched(ws, session, opus_packets)

    if not session.tts_abort:
        await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        logger.info(f"[{sid}] TTS complete: {sent}/{len(opus_packets)} packets")
    else:
        logger.info(f"[{sid}] TTS aborted: {sent}/{len(opus_packets)} packets")

    return sent


async def _decode_and_asr(ws, session) -> Optional[str]:
    """Decode Opus → ASR. Returns transcribed text or None."""
    sid = session.session_id

    logger.info(f"[{sid}] Pipeline start: {len(session.opus_packets)} opus packets")

    # --- Opus decode ---
    t0 = time.monotonic()
    try:
        decoder = opuslib.Decoder(settings.pcm_sample_rate, settings.pcm_channels)
        pcm_frames = []
        for packet in session.opus_packets:
            pcm_frame = decoder.decode(packet, 960)
            pcm_frames.append(pcm_frame)
        pcm = b''.join(pcm_frames)
        logger.info(f"[{sid}] Opus decode: {len(session.opus_packets)} packets -> {len(pcm)} bytes ({time.monotonic()-t0:.2f}s)")
    except Exception as e:
        logger.error(f"[{sid}] Opus decode failed: {e}")
        await ws_send_safe(ws, json.dumps({"type": "error", "message": f"Opus decode failed: {e}"}), session)
        return None

    if session.tts_abort or ws.closed:
        return None

    # Meeting: accumulate audio if recording active
    if session.meeting_active:
        session._meeting_audio_buffer.extend(pcm)
        logger.info(f"[{sid}] Meeting: accumulated {len(pcm)} bytes ({len(session._meeting_audio_buffer)} total)")

    # --- ASR ---
    t0 = time.monotonic()
    try:
        text = await transcribe_pcm(pcm, session=session)
        logger.info(f"[{sid}] ASR: '{text}' ({time.monotonic()-t0:.2f}s)")
    except Exception as e:
        logger.error(f"[{sid}] ASR failed: {e}")
        await ws_send_safe(ws, json.dumps({"type": "error", "message": f"ASR failed: {e}"}), session)
        return None

    await ws_send_safe(ws, json.dumps({"type": "asr_text", "text": text}), session, "asr_text")

    if not text or text.strip() == "":
        logger.info(f"[{sid}] ASR empty, skipping")
        return None

    return text


async def _process_and_speak(ws, session, text: str):
    """Route text → tool call → handle result.

    Flow:
    1. Check pending follow-up (ask_user)
    2. Try rule-based router (fast, no LLM)
    3. Fall back to LLM planner
    4. Execute tool + handle result
    """
    sid = session.session_id

    if session.tts_abort or ws.closed:
        return

    tool_name = None
    args = {}
    reply_hint = ""
    emotion = ""

    # 1. Check pending follow-up from previous ask_user
    if session._pending_tool_call:
        pending = session._pending_tool_call
        session._pending_tool_call = None
        pending["partial_args"][pending["missing_param"]] = text
        tool_name = pending["tool"]
        args = pending["partial_args"]
        logger.info(f"[{sid}] Follow-up: {tool_name}, filled {pending['missing_param']}={text}")

    # 2. Try rule-based router (< 10ms, no LLM)
    if not tool_name:
        match = route_intent(text)
        if match:
            tool_name = match.tool
            args = match.args
            reply_hint = match.reply_hint
            emotion = _infer_emotion(tool_name)
            # Add user+hint to conversation history (LLM path handles its own)
            append_user_message(session.device_id, text)
            if reply_hint:
                append_assistant_message(session.device_id, reply_hint)
            logger.info(f"[{sid}] Router: {tool_name}")

    # 3. Fall back to LLM planner
    if not tool_name:
        t0 = time.monotonic()
        try:
            intent = await plan_intent(text, session_id=sid, session=session)
        except Exception as e:
            logger.error(f"[{sid}] LLM failed: {e}", exc_info=True)
            await ws_send_safe(ws, json.dumps({"type": "error", "message": f"LLM failed: {e}"}), session)
            return
        tool_name = intent.get("tool", "chat")
        args = intent.get("args", {})
        reply_hint = intent.get("reply_hint", "")
        emotion = intent.get("emotion", "")
        logger.info(f"[{sid}] LLM: tool={tool_name} emotion={emotion} ({time.monotonic()-t0:.2f}s)")

    if session.tts_abort or ws.closed:
        return

    # 4. Validate tool exists (LLM might hallucinate a tool name)
    if tool_name != "chat" and not get_tool(tool_name):
        logger.warning(f"[{sid}] Unknown tool '{tool_name}', falling back to chat")
        # Re-run through LLM as chat
        tool_name = "chat"
        args = {"response": args.get("reply_hint", args.get("response", "抱歉，我不太明白你的意思。"))}

    # Track whether music was paused for this interaction (for auto-resume)
    music_was_paused = session.music_playing and session.music_paused

    # Send expression to device (before TTS, so eyes change as voice starts)
    if emotion:
        await _send_expression(ws, session, emotion)

    # 5. Handle "chat" (direct response, no tool execution)
    if tool_name == "chat":
        reply = args.get("response", "")
        if not reply:
            _auto_resume_music(session, music_was_paused)
            return
        try:
            opus_packets = await synthesize_tts(reply, session=session)
            logger.info(f"[{sid}] TTS synth: {len(opus_packets)} packets")
        except Exception as e:
            logger.error(f"[{sid}] TTS failed: {e}")
            await ws_send_safe(ws, json.dumps({"type": "error", "message": f"TTS failed: {e}"}), session)
            _auto_resume_music(session, music_was_paused)
            return
        if session.tts_abort or ws.closed:
            _auto_resume_music(session, music_was_paused)
            return
        await _send_tts_round(ws, session, opus_packets, reply)
        _auto_resume_music(session, music_was_paused)
        return

    # 6. Execute tool
    # For tools with a hint, speak it first in a TTS session
    tts_session_open = False
    if reply_hint:
        try:
            hint_packets = await synthesize_tts(reply_hint, session=session)
            ok = await ws_send_safe(ws, json.dumps({"type": "tts_start", "text": reply_hint}), session, "tts_start")
            if ok:
                tts_session_open = True
                await _stream_batched(ws, session, hint_packets)
        except Exception as e:
            logger.warning(f"[{sid}] Hint TTS failed: {e}")

    if session.tts_abort or ws.closed:
        if tts_session_open:
            await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        return

    result = await execute_tool(
        tool_name, args, session,
        ws=ws, ws_send_fn=ws_send_safe,
    )

    # 6. Handle tool result
    if result.type == "music":
        # Close hint TTS session before entering music mode
        if tts_session_open:
            await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
            tts_session_open = False
        title = result.data.get("title", "")
        generator = result.data.get("generator")
        if generator:
            await _stream_music(ws, session, title, generator)

    elif result.type == "tts":
        try:
            result_packets = await synthesize_tts(result.text, session=session)
        except Exception as e:
            logger.error(f"[{sid}] Result TTS failed: {e}")
            if tts_session_open:
                await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
            return
        if tts_session_open:
            await _stream_batched(ws, session, result_packets)
            await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        else:
            await _send_tts_round(ws, session, result_packets, result.text)
        tts_session_open = False
        # Capture tool result in conversation history
        append_assistant_message(session.device_id, result.text)

    elif result.type == "ask_user":
        if tts_session_open:
            await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
            tts_session_open = False
        try:
            q_packets = await synthesize_tts(result.text, session=session)
            await _send_tts_round(ws, session, q_packets, result.text)
        except Exception:
            pass
        session._pending_tool_call = result.data

    elif result.type == "error":
        error_text = result.text or "抱歉，执行失败了。"
        try:
            err_packets = await synthesize_tts(error_text, session=session)
        except Exception:
            if tts_session_open:
                await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
            return
        if tts_session_open:
            await _stream_batched(ws, session, err_packets)
            await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        else:
            await _send_tts_round(ws, session, err_packets, error_text)
        tts_session_open = False

    else:  # "silent" or unknown
        if tts_session_open:
            await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")

    # Auto-resume music if it was paused for this interaction and tool wasn't player-related
    if not tool_name.startswith("player.") and tool_name != "youtube.play":
        _auto_resume_music(session, music_was_paused)


def _auto_resume_music(session: Session, was_paused: bool):
    """Resume music playback if it was paused for a voice interaction."""
    if was_paused and session.music_playing and session.music_paused:
        session._music_pause_event.set()
        session.music_paused = False
        logger.info(f"[{session.session_id}] Music auto-resumed after interaction")


async def _stream_music(ws, session, title: str, generator):
    """Stream music Opus packets from async generator to device.

    Supports pause/resume via session._music_pause_event.
    """
    sid = session.session_id

    session.music_playing = True
    session.music_paused = False
    session.music_abort = False
    session.music_title = title
    session._music_pause_event.set()
    session._music_task = asyncio.current_task()

    await ws_send_safe(ws, json.dumps({
        "type": "music_start", "title": title
    }), session, "music_start")

    logger.info(f"[{sid}] Music streaming: '{title}'")

    try:
        batch = []
        sent = 0
        batch_count = 0
        pacing_start = None

        async for opus_packet in generator:
            if session.music_abort or ws.closed:
                break

            if not session._music_pause_event.is_set():
                logger.info(f"[{sid}] Music paused at packet {sent}")
                await session._music_pause_event.wait()
                if session.music_abort or ws.closed:
                    break
                logger.info(f"[{sid}] Music resumed at packet {sent}")
                await ws_send_safe(ws, json.dumps({"type": "music_resume"}), session, "music_resume")
                # Reset pacing after resume
                pacing_start = None
                batch_count = 0

            batch.append(opus_packet)
            if len(batch) >= BATCH_SIZE:
                blob = b''
                for pkt in batch:
                    blob += struct.pack('>H', len(pkt)) + pkt
                ok = await ws_send_safe(ws, blob, session, "music_batch",
                                       timeout=MUSIC_WS_SEND_TIMEOUT)
                if not ok:
                    break
                sent += len(batch)
                batch = []
                batch_count += 1

                # Pre-buffer: send first N batches without delay
                if batch_count < MUSIC_PRE_BUFFER_BATCHES:
                    continue

                # Wall-clock pacing for music
                if pacing_start is None:
                    pacing_start = time.monotonic()

                paced_idx = batch_count - MUSIC_PRE_BUFFER_BATCHES
                target = pacing_start + (paced_idx + 1) * MUSIC_BATCH_PERIOD
                now = time.monotonic()
                if now < target:
                    await asyncio.sleep(target - now)

        if batch and not session.music_abort and not ws.closed:
            blob = b''
            for pkt in batch:
                blob += struct.pack('>H', len(pkt)) + pkt
            await ws_send_safe(ws, blob, session, "music_batch_final",
                               timeout=MUSIC_WS_SEND_TIMEOUT)
            sent += len(batch)

    except asyncio.CancelledError:
        logger.info(f"[{sid}] Music task cancelled")
    except Exception as e:
        logger.error(f"[{sid}] Music streaming error: {e}")
    finally:
        session.music_playing = False
        session.music_paused = False
        session._music_task = None
        if not ws.closed:
            await ws_send_safe(ws, json.dumps({"type": "music_end"}), session, "music_end")
        logger.info(f"[{sid}] Music ended: '{title}', {sent} packets sent")


# ── Expression helpers ──────────────────────────────────────

# Default emotion for router-matched tools (no LLM to generate emotion)
_TOOL_EMOTIONS = {
    "youtube.play": "happy",
    "player.pause": "neutral",
    "player.resume": "happy",
    "player.stop": "neutral",
    "reminder.set": "happy",
    "meeting.start": "neutral",
    "meeting.end": "happy",
    "meeting.transcribe": "thinking",
    "weather.query": "thinking",
    "timer.set": "happy",
    "web.search": "thinking",
    "note.save": "happy",
    "conversation.reset": "wink",
}


def _infer_emotion(tool_name: str) -> str:
    """Infer emotion from tool name (for router-matched paths without LLM)."""
    return _TOOL_EMOTIONS.get(tool_name, "")


async def _send_expression(ws, session, emotion: str, duration_ms: int = 3000):
    """Send expression command to device."""
    if not emotion or emotion == "neutral":
        return
    msg = {"type": "expression", "expr": emotion, "duration_ms": duration_ms}
    await ws_send_safe(ws, json.dumps(msg), session, f"expr:{emotion}")
    logger.info(f"[{session.session_id}] Expression: {emotion}")


BATCH_SIZE = 10
PRE_BUFFER_BATCHES = 2   # Send first 2 batches without delay (1.2s device buffer)
FRAME_MS = 0.060          # 60ms per Opus frame
PACING_FACTOR = 0.85      # Send at 85% of real-time (slightly faster than playback)
BATCH_PERIOD = BATCH_SIZE * FRAME_MS * PACING_FACTOR  # ~510ms between paced batches

MUSIC_PRE_BUFFER_BATCHES = 2  # 2 batches × 10 × 60ms = 1.2s pre-buffer (matches device queue=24)
MUSIC_BATCH_PERIOD = BATCH_SIZE * FRAME_MS * 0.80  # ~480ms — send 20% faster to build margin


async def _stream_batched(
    ws: WebSocketServerProtocol,
    session: Session,
    opus_packets: List[bytes],
) -> int:
    """Send Opus packets in batches with pre-buffer + wall-clock pacing.

    Pre-buffer: first PRE_BUFFER_BATCHES sent without delay to fill device queue.
    Paced phase: wall-clock scheduling at BATCH_PERIOD intervals, automatically
    compensating for ws.send() latency (no fixed sleep after each send).
    """
    sid = session.session_id
    total = len(opus_packets)
    num_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    logger.info(f"[{sid}] TTS stream: {total} packets in {num_batches} batches "
                f"(pre_buffer={PRE_BUFFER_BATCHES}, period={BATCH_PERIOD*1000:.0f}ms)")

    sent = 0
    t0 = time.monotonic()
    pacing_start = None  # Wall-clock start of paced phase

    for batch_idx in range(num_batches):
        if session.tts_abort or ws.closed:
            logger.info(f"[{sid}] TTS aborted at batch {batch_idx}/{num_batches}")
            break

        start = batch_idx * BATCH_SIZE
        batch = opus_packets[start:start + BATCH_SIZE]

        blob = b''
        for pkt in batch:
            blob += struct.pack('>H', len(pkt)) + pkt

        send_t0 = time.monotonic()
        ok = await ws_send_safe(ws, blob, session, f"batch#{batch_idx}")
        send_dt = time.monotonic() - send_t0

        if ok:
            sent += len(batch)
            if batch_idx < PRE_BUFFER_BATCHES or batch_idx % 5 == 0 or batch_idx == num_batches - 1:
                logger.info(f"[{sid}] Batch {batch_idx}/{num_batches}: "
                            f"{len(batch)} pkts, {len(blob)}B, send={send_dt*1000:.0f}ms")
        else:
            logger.error(f"[{sid}] Batch {batch_idx} send FAILED after {send_dt:.1f}s")
            break

        # Pre-buffer phase: send without delay
        if batch_idx < PRE_BUFFER_BATCHES - 1:
            continue

        # Start wall-clock pacing after pre-buffer completes
        if pacing_start is None:
            pacing_start = time.monotonic()

        # Schedule next batch by wall-clock (compensates for ws.send latency)
        if batch_idx < num_batches - 1:
            paced_idx = batch_idx - PRE_BUFFER_BATCHES + 1
            target = pacing_start + (paced_idx + 1) * BATCH_PERIOD
            now = time.monotonic()
            if now < target:
                await asyncio.sleep(target - now)
            # If now >= target, we're behind schedule — send immediately

    elapsed = time.monotonic() - t0
    logger.info(f"[{sid}] TTS batched: {sent}/{total} in {elapsed:.1f}s "
                f"({num_batches} batches)")
    return sent
