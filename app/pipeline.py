"""ASR → LLM → TTS pipeline with batched audio streaming.

Batching reduces TCP segments through phone hotspot: instead of 117 individual
WebSocket messages (one per Opus packet), we send ~12 batched messages (10 packets
each). Each batch is ~2KB — fits in one TCP segment and one ESP32 WS buffer read.
"""
import asyncio
import json
import logging
import struct
import time
from datetime import datetime
from typing import Optional, Tuple, List

import opuslib
from websockets.server import WebSocketServerProtocol

from .config import settings
from .session import Session
from .asr import transcribe_pcm
from .tts import synthesize_tts
from .llm import call_llm, plan_intent, call_llm_chat
from .openclaw import execute_task, is_configured as openclaw_configured
from .music import search_and_stream

logger = logging.getLogger(__name__)

WS_SEND_TIMEOUT = 2.0  # seconds


async def ws_send_safe(ws: WebSocketServerProtocol, data, session: Session, label: str = "") -> bool:
    """Send data via WebSocket with timeout. Returns True on success."""
    try:
        await asyncio.wait_for(ws.send(data), timeout=WS_SEND_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        logger.error(f"[{session.session_id}] ws.send() timed out ({WS_SEND_TIMEOUT}s) {label}")
        return False
    except Exception as e:
        logger.warning(f"[{session.session_id}] ws.send() failed {label}: {type(e).__name__}: {e}")
        return False


async def run_pipeline(ws: WebSocketServerProtocol, session: Session):
    """Full pipeline: decode Opus → ASR → LLM → TTS → rate-controlled send.

    Runs as a background task so the WS message loop stays responsive
    for abort messages during TTS streaming.
    """
    if not session.opus_packets:
        await ws_send_safe(ws, json.dumps({"type": "error", "message": "empty audio"}), session)
        return

    session.processing = True
    pipeline_t0 = time.monotonic()

    try:
        # Start keepalive pings to keep TCP cwnd open during processing
        keepalive_task = asyncio.create_task(_keepalive_pings(ws, session))

        try:
            # Phase 1: Decode + ASR + Intent planning
            asr_result = await _decode_and_asr(ws, session)
            if asr_result is None:
                return

            text = asr_result

            # Phase 2: LLM intent → optional hint TTS → optional OpenClaw → result TTS
            await _process_and_speak(ws, session, text)
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
                logger.debug(f"[{session.session_id}] Keepalive ping sent")
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
        logger.info(f"[{sid}] ASR empty, skipping LLM+TTS")
        return None

    return text


def _encode_silence_packet() -> bytes:
    """Encode a single 60ms silence Opus packet for keepalive during OpenClaw."""
    encoder = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
    silence_pcm = b'\x00' * (960 * 2)  # 960 samples * 2 bytes = 60ms
    return encoder.encode(silence_pcm, 960)


# Pre-encode silence at module load
_SILENCE_OPUS = _encode_silence_packet()


async def _process_and_speak(ws, session, text: str):
    """Intent planning → TTS → optional OpenClaw with keepalive → result TTS.

    For EXECUTE intents: uses a SINGLE tts_start...tts_end session:
      tts_start → hint audio → [silence keepalive every 2s] → result audio → tts_end
    Device stays in SPEAKING state the entire time (no 5s timeout trigger).
    """
    sid = session.session_id

    if session.tts_abort or ws.closed:
        return

    # --- Intent planning (fast, ~1s) ---
    if openclaw_configured(session=session):
        t0 = time.monotonic()
        intent = await plan_intent(text, session_id=sid, session=session)
        action = intent.get("action", "chat")
        logger.info(f"[{sid}] Intent: {action} ({time.monotonic()-t0:.2f}s)")

        if action == "execute":
            await _execute_with_hint(ws, session, intent)
            return
        elif action == "music":
            await _play_music(ws, session, intent)
            return
        elif action == "remind":
            reply = await _handle_remind(session, intent)
        elif action == "music_stop":
            # Stop any active music
            if session.music_playing:
                session.music_abort = True
                session._music_pause_event.set()  # Unblock if paused
            reply = intent.get("response", "Music stopped.")
        elif action == "music_pause":
            if session.music_playing:
                session._music_pause_event.clear()
                session.music_paused = True
            reply = intent.get("response", "Music paused.")
        else:
            reply = intent.get("response", "I'm not sure how to help with that.")
    else:
        # Simple chat mode (no OpenClaw)
        t0 = time.monotonic()
        try:
            reply = await call_llm_chat(text, session_id=sid, session=session)
            logger.info(f"[{sid}] LLM chat: '{reply}' ({time.monotonic()-t0:.2f}s)")
        except Exception as e:
            logger.error(f"[{sid}] LLM failed: {e}", exc_info=True)
            await ws_send_safe(ws, json.dumps({"type": "error", "message": f"LLM failed: {e}"}), session)
            return

    if session.tts_abort or ws.closed:
        return

    # --- Normal TTS for chat responses ---
    try:
        opus_packets = await synthesize_tts(reply, session=session)
        logger.info(f"[{sid}] TTS synth: {len(opus_packets)} packets")
    except Exception as e:
        logger.error(f"[{sid}] TTS synth failed: {e}")
        await ws_send_safe(ws, json.dumps({"type": "error", "message": f"TTS failed: {e}"}), session)
        return

    if session.tts_abort or ws.closed:
        return

    await _send_tts_round(ws, session, opus_packets, reply)


async def _handle_remind(session: Session, intent: dict) -> str:
    """Handle REMIND intent: parse datetime, save to DB, return confirmation."""
    sid = session.session_id
    dt_str = intent.get("datetime", "")
    message = intent.get("message", "Reminder")
    reply = intent.get("response", "Reminder set.")

    try:
        remind_at = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        logger.warning(f"[{sid}] Invalid remind datetime: {dt_str}")
        return "Sorry, I couldn't understand that date. Please try again."

    if remind_at < datetime.now():
        logger.warning(f"[{sid}] Remind datetime in past: {dt_str}")
        return "That time has already passed. Please set a future reminder."

    try:
        from .database import async_session_factory
        from .models import Reminder

        async with async_session_factory() as db:
            reminder = Reminder(
                user_id=session.config.user_id if session.config.user_id else None,
                device_id=session.device_id,
                remind_at=remind_at,
                message=message,
            )
            db.add(reminder)
            await db.commit()
            logger.info(f"[{sid}] Reminder saved: '{message}' at {remind_at}")
    except Exception as e:
        logger.error(f"[{sid}] Failed to save reminder: {e}")
        return "Sorry, I couldn't save the reminder. Please try again."

    return reply


async def _play_music(ws, session, intent: dict):
    """Handle MUSIC intent: hint TTS → fetch audio → stream Opus batches.

    Supports pause/resume via session._music_pause_event.
    """
    sid = session.session_id
    query = intent.get("query", "")
    hint = intent.get("reply_hint", "Playing music...")

    # --- Send hint via normal TTS round ---
    try:
        hint_packets = await synthesize_tts(hint, session=session)
        await _send_tts_round(ws, session, hint_packets, hint)
    except Exception as e:
        logger.warning(f"[{sid}] Music hint TTS failed: {e}")

    if session.tts_abort or ws.closed:
        return

    # --- Fetch and stream music ---
    try:
        title, generator = await search_and_stream(query)
    except Exception as e:
        logger.error(f"[{sid}] Music fetch failed: {e}")
        try:
            err_packets = await synthesize_tts("Sorry, I couldn't find that music.", session=session)
            await _send_tts_round(ws, session, err_packets, "error")
        except Exception:
            pass
        return

    session.music_playing = True
    session.music_paused = False
    session.music_abort = False
    session.music_title = title
    session._music_pause_event.set()
    session._music_task = asyncio.current_task()

    # Send music_start to device
    await ws_send_safe(ws, json.dumps({
        "type": "music_start", "title": title
    }), session, "music_start")

    logger.info(f"[{sid}] Music streaming: '{title}'")

    try:
        batch = []
        sent = 0
        async for opus_packet in generator:
            if session.music_abort or ws.closed:
                break

            # Check pause
            if not session._music_pause_event.is_set():
                logger.info(f"[{sid}] Music paused at packet {sent}")
                await session._music_pause_event.wait()
                if session.music_abort or ws.closed:
                    break
                logger.info(f"[{sid}] Music resumed at packet {sent}")
                # Tell device to re-enter MUSIC state
                await ws_send_safe(ws, json.dumps({"type": "music_resume"}), session, "music_resume")

            batch.append(opus_packet)
            if len(batch) >= BATCH_SIZE:
                blob = b''
                for pkt in batch:
                    blob += struct.pack('>H', len(pkt)) + pkt
                ok = await ws_send_safe(ws, blob, session, "music_batch")
                if not ok:
                    break
                sent += len(batch)
                batch = []
                await asyncio.sleep(BATCH_INTERVAL)

        # Send remaining
        if batch and not session.music_abort and not ws.closed:
            blob = b''
            for pkt in batch:
                blob += struct.pack('>H', len(pkt)) + pkt
            await ws_send_safe(ws, blob, session, "music_batch_final")
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


async def _execute_with_hint(ws, session, intent: dict):
    """Handle EXECUTE intent: hint → silence keepalive → result, all in one TTS session.

    Timeline:
      [0s]  tts_start (hint text)
      [0s]  hint opus packets (~2s audio)
      [2s]  silence packet every 2s while OpenClaw works
      [30s] result opus packets
      [35s] tts_end
    """
    sid = session.session_id
    hint = intent.get("reply_hint", "Processing your request...")
    task = intent.get("task", "")

    # --- Synthesize hint audio ---
    try:
        hint_packets = await synthesize_tts(hint, session=session)
        logger.info(f"[{sid}] Hint TTS: '{hint}' ({len(hint_packets)} packets)")
    except Exception as e:
        logger.warning(f"[{sid}] Hint TTS failed: {e}")
        hint_packets = []

    # --- Open single TTS session ---
    ok = await ws_send_safe(ws, json.dumps({"type": "tts_start", "text": hint}), session, "tts_start")
    if not ok:
        return

    # --- Send hint audio ---
    if hint_packets:
        await _stream_batched(ws, session, hint_packets)

    if session.tts_abort or ws.closed:
        await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        return

    # --- Execute OpenClaw with silence keepalive ---
    t0 = time.monotonic()
    logger.info(f"[{sid}] OpenClaw starting (sending silence keepalive every 2s)...")
    openclaw_task = asyncio.create_task(execute_task(task, session=session))

    silence_blob = struct.pack('>H', len(_SILENCE_OPUS)) + _SILENCE_OPUS

    while not openclaw_task.done():
        if session.tts_abort or ws.closed:
            openclaw_task.cancel()
            break
        try:
            await asyncio.wait_for(asyncio.shield(openclaw_task), timeout=2.0)
        except asyncio.TimeoutError:
            # OpenClaw still working — send silence to prevent device 5s timeout
            await ws_send_safe(ws, silence_blob, session, "silence")
            logger.info(f"[{sid}] Silence keepalive sent ({time.monotonic()-t0:.0f}s)")

    if session.tts_abort or ws.closed:
        await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        return

    # --- Get OpenClaw result ---
    try:
        result = openclaw_task.result()
        logger.info(f"[{sid}] OpenClaw: '{result[:80]}' ({time.monotonic()-t0:.1f}s)")
    except Exception as e:
        logger.error(f"[{sid}] OpenClaw failed: {e}")
        result = "Sorry, I couldn't complete that task right now."

    # --- Synthesize result audio with silence keepalive (TTS API can take 3-5s) ---
    tts_task = asyncio.create_task(synthesize_tts(result, session=session))

    while not tts_task.done():
        if session.tts_abort or ws.closed:
            tts_task.cancel()
            break
        try:
            await asyncio.wait_for(asyncio.shield(tts_task), timeout=2.0)
        except asyncio.TimeoutError:
            await ws_send_safe(ws, silence_blob, session, "silence")
            logger.info(f"[{sid}] Silence keepalive during TTS synth ({time.monotonic()-t0:.0f}s)")

    if session.tts_abort or ws.closed:
        await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        return

    try:
        result_packets = tts_task.result()
        logger.info(f"[{sid}] Result TTS: {len(result_packets)} packets")
    except Exception as e:
        logger.error(f"[{sid}] Result TTS failed: {e}")
        await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        return

    if session.tts_abort or ws.closed:
        await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
        return

    await _stream_batched(ws, session, result_packets)

    # --- Close TTS session ---
    await ws_send_safe(ws, json.dumps({"type": "tts_end"}), session, "tts_end")
    logger.info(f"[{sid}] Execute pipeline complete: hint + {len(result_packets)} result packets")


BATCH_SIZE = 10       # 10 opus packets per WS message (~2KB, fits in one TCP segment)
BATCH_INTERVAL = 0.5  # 500ms between batches (10×60ms=600ms audio per batch = 1.2× real-time)


async def _stream_batched(
    ws: WebSocketServerProtocol,
    session: Session,
    opus_packets: List[bytes],
) -> int:
    """Send Opus packets in batches to reduce TCP segments through phone hotspot.

    Format per WS binary message: [2B BE len][data][2B BE len][data]...
    Device parses length-prefixed packets from each batch.
    """
    sid = session.session_id
    total = len(opus_packets)
    num_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    logger.info(f"[{sid}] TTS stream: {total} packets in {num_batches} batches "
                f"(batch_size={BATCH_SIZE}, interval={BATCH_INTERVAL}s)")

    sent = 0
    t0 = time.monotonic()

    for batch_idx in range(num_batches):
        if session.tts_abort or ws.closed:
            logger.info(f"[{sid}] TTS aborted at batch {batch_idx}/{num_batches}")
            break

        start = batch_idx * BATCH_SIZE
        batch = opus_packets[start:start + BATCH_SIZE]

        # Pack: [2-byte BE length][opus data] for each packet
        blob = b''
        for pkt in batch:
            blob += struct.pack('>H', len(pkt)) + pkt

        send_t0 = time.monotonic()
        ok = await ws_send_safe(ws, blob, session, f"batch#{batch_idx}")
        send_dt = time.monotonic() - send_t0

        if ok:
            sent += len(batch)
            logger.info(f"[{sid}] Batch {batch_idx}/{num_batches}: "
                        f"{len(batch)} pkts, {len(blob)}B, send={send_dt*1000:.0f}ms")
        else:
            logger.error(f"[{sid}] Batch {batch_idx} send FAILED after {send_dt:.1f}s")
            break

        # Pace between batches (skip after last batch)
        if batch_idx < num_batches - 1:
            await asyncio.sleep(BATCH_INTERVAL)

    elapsed = time.monotonic() - t0
    logger.info(f"[{sid}] TTS batched: {sent}/{total} in {elapsed:.1f}s "
                f"({num_batches} batches)")
    return sent
