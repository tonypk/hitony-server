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
from typing import Optional, Tuple, List

import opuslib
from websockets.server import WebSocketServerProtocol

from .config import settings
from .session import Session
from .asr import transcribe_pcm
from .tts import synthesize_tts
from .llm import call_llm, plan_intent, call_llm_chat
from .openclaw import execute_task, is_configured as openclaw_configured

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
        text = await transcribe_pcm(pcm)
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


async def _process_and_speak(ws, session, text: str):
    """Intent planning → optional hint TTS → OpenClaw/chat → result TTS.

    For EXECUTE intents: immediately speaks the hint (e.g. "Searching for news...")
    so the user gets feedback while OpenClaw works (30-90s).
    """
    sid = session.session_id

    if session.tts_abort or ws.closed:
        return

    # --- Intent planning (fast, ~1s) ---
    if openclaw_configured():
        t0 = time.monotonic()
        intent = await plan_intent(text, session_id=sid)
        action = intent.get("action", "chat")
        logger.info(f"[{sid}] Intent: {action} ({time.monotonic()-t0:.2f}s)")

        if action == "execute":
            hint = intent.get("reply_hint", "Processing your request...")
            task = intent.get("task", text)

            # --- Phase 1: Speak the hint immediately ---
            logger.info(f"[{sid}] Hint TTS: '{hint}'")
            try:
                hint_packets = await synthesize_tts(hint)
                await _send_tts_round(ws, session, hint_packets, hint)
            except Exception as e:
                logger.warning(f"[{sid}] Hint TTS failed: {e}")

            if session.tts_abort or ws.closed:
                return

            # --- Phase 2: Execute OpenClaw (slow, 10-90s) ---
            t0 = time.monotonic()
            try:
                result = await execute_task(task)
                logger.info(f"[{sid}] OpenClaw: '{result[:80]}' ({time.monotonic()-t0:.1f}s)")
            except Exception as e:
                logger.error(f"[{sid}] OpenClaw failed: {e}")
                result = "Sorry, I couldn't complete that task right now."

            if session.tts_abort or ws.closed:
                return

            reply = result
        else:
            reply = intent.get("response", "I'm not sure how to help with that.")
    else:
        # Simple chat mode (no OpenClaw)
        t0 = time.monotonic()
        try:
            reply = await call_llm_chat(text, session_id=sid)
            logger.info(f"[{sid}] LLM chat: '{reply}' ({time.monotonic()-t0:.2f}s)")
        except Exception as e:
            logger.error(f"[{sid}] LLM failed: {e}", exc_info=True)
            await ws_send_safe(ws, json.dumps({"type": "error", "message": f"LLM failed: {e}"}), session)
            return

    if session.tts_abort or ws.closed:
        return

    # --- Final TTS: speak the result ---
    try:
        opus_packets = await synthesize_tts(reply)
        logger.info(f"[{sid}] TTS synth: {len(opus_packets)} packets")
    except Exception as e:
        logger.error(f"[{sid}] TTS synth failed: {e}")
        await ws_send_safe(ws, json.dumps({"type": "error", "message": f"TTS failed: {e}"}), session)
        return

    if session.tts_abort or ws.closed:
        return

    await _send_tts_round(ws, session, opus_packets, reply)


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
