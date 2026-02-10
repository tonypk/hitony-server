"""WebSocket server using websockets library (xiaozhi-compatible)"""
import asyncio
import json
import logging
import uuid
from typing import Optional, List
import websockets
from websockets.server import WebSocketServerProtocol
import opuslib

from .config import settings
from .protocol import AsrText, TtsStart, TtsEnd, ErrorMsg
from .registry import registry
from .asr import transcribe_pcm
from .tts import synthesize_tts
from .openclaw import call_openclaw

logger = logging.getLogger(__name__)


class ConnState:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.session_id = str(uuid.uuid4())[:8]
        self.opus_packets: List[bytes] = []
        self.listening = False
        self.tts_abort = False  # Abort flag for TTS streaming
        self.processing = False  # Whether currently processing audio pipeline


async def handle_text_message(ws: WebSocketServerProtocol, state: ConnState, text: str):
    """Handle text JSON messages from device"""
    try:
        payload = json.loads(text)
    except Exception:
        await ws.send(json.dumps({"type": "error", "message": "invalid json"}))
        return

    mtype = payload.get("type")
    logger.info(f"[{state.session_id}] Device {state.device_id}: {mtype}")

    if mtype == "hello":
        # Respond with session info and audio parameters
        hello_resp = {
            "type": "hello",
            "session_id": state.session_id,
            "audio_params": {
                "sample_rate": settings.pcm_sample_rate,
                "channels": settings.pcm_channels,
                "codec": "opus",
                "frame_duration_ms": 60,
            },
            "features": {
                "asr": True,
                "tts": True,
                "llm": True,
                "abort": True,
            },
            "version": 1,
        }
        await ws.send(json.dumps(hello_resp))
        logger.info(f"[{state.session_id}] Hello handshake complete")
        return

    elif mtype == "audio_start":
        state.opus_packets = []
        state.listening = True
        state.tts_abort = False  # Reset abort flag for new interaction
        return

    elif mtype == "audio_end":
        state.listening = False
        await process_audio(ws, state)
        return

    elif mtype == "abort":
        # Client requests TTS abort (user started speaking during playback)
        logger.info(f"[{state.session_id}] Abort requested by device")
        state.tts_abort = True
        # Acknowledge abort
        await ws.send(json.dumps({"type": "tts_end", "reason": "abort"}))
        return

    elif mtype == "ping":
        await ws.send(json.dumps({"type": "pong"}))
        return


async def handle_binary_message(ws: WebSocketServerProtocol, state: ConnState, chunk: bytes):
    """Accumulate Opus audio packets"""
    if not state.listening:
        return
    state.opus_packets.append(bytes(chunk))


async def process_audio(ws: WebSocketServerProtocol, state: ConnState):
    """Process accumulated audio: Opus decode -> ASR -> LLM -> TTS"""
    if not state.opus_packets:
        await ws.send(json.dumps({"type": "error", "message": "empty audio"}))
        return

    state.processing = True

    # Decode Opus packets to raw PCM
    try:
        decoder = opuslib.Decoder(settings.pcm_sample_rate, settings.pcm_channels)
        pcm_frames = []
        for packet in state.opus_packets:
            pcm_frame = decoder.decode(packet, 960)  # 960 samples = 60ms @ 16kHz
            pcm_frames.append(pcm_frame)
        pcm = b''.join(pcm_frames)
        logger.info(f"[{state.session_id}] Decoded {len(state.opus_packets)} Opus packets to {len(pcm)} bytes PCM")
    except Exception as e:
        logger.error(f"[{state.session_id}] Opus decode failed: {e}")
        await ws.send(json.dumps({"type": "error", "message": f"Opus decode failed: {e}"}))
        state.processing = False
        return

    # Check abort before ASR
    if state.tts_abort:
        logger.info(f"[{state.session_id}] Aborted before ASR")
        state.processing = False
        return

    # ASR
    try:
        text = await transcribe_pcm(pcm)
        logger.info(f"[{state.session_id}] ASR result: {text}")
    except Exception as e:
        logger.error(f"[{state.session_id}] ASR failed: {e}")
        await ws.send(json.dumps({"type": "error", "message": f"ASR failed: {e}"}))
        state.processing = False
        return

    await ws.send(json.dumps({"type": "asr_text", "text": text}))

    # Skip LLM+TTS if ASR returned empty text (noise/silence)
    if not text or text.strip() == "":
        logger.info(f"[{state.session_id}] ASR returned empty text, skipping LLM+TTS")
        state.processing = False
        return

    # Check abort before LLM
    if state.tts_abort:
        logger.info(f"[{state.session_id}] Aborted before LLM")
        state.processing = False
        return

    # LLM via OpenAI GPT
    try:
        reply = await call_openclaw(text)
        logger.info(f"[{state.session_id}] LLM reply: {reply}")
    except Exception as e:
        logger.error(f"[{state.session_id}] LLM failed: {e}", exc_info=True)
        await ws.send(json.dumps({"type": "error", "message": f"LLM failed: {e}"}))
        state.processing = False
        return

    # Check abort before TTS
    if state.tts_abort:
        logger.info(f"[{state.session_id}] Aborted before TTS")
        state.processing = False
        return

    # TTS - returns Opus packets
    try:
        opus_packets = await synthesize_tts(reply)
        packet_sizes = [len(p) for p in opus_packets[:10]]
        logger.info(f"[{state.session_id}] TTS synthesized {len(opus_packets)} Opus packets, first 10 sizes: {packet_sizes}")
    except Exception as e:
        logger.error(f"[{state.session_id}] TTS failed: {e}")
        await ws.send(json.dumps({"type": "error", "message": f"TTS failed: {e}"}))
        state.processing = False
        return

    # Stream TTS with abort checking
    await ws.send(json.dumps({"type": "tts_start", "text": reply}))

    sent_count = 0
    for packet in opus_packets:
        if state.tts_abort:
            logger.info(f"[{state.session_id}] TTS aborted after {sent_count}/{len(opus_packets)} packets")
            break
        await ws.send(packet)
        sent_count += 1
        await asyncio.sleep(0.06)  # Match 60ms Opus frame duration for realtime playback

    if not state.tts_abort:
        await ws.send(json.dumps({"type": "tts_end"}))
        logger.info(f"[{state.session_id}] TTS complete: {sent_count} packets sent")
    else:
        logger.info(f"[{state.session_id}] TTS aborted, sent tts_end already via abort handler")

    state.processing = False


async def handle_client(ws: WebSocketServerProtocol, path: str):
    """Main WebSocket connection handler"""
    # Extract device_id and token from headers
    device_id = ws.request_headers.get("x-device-id")
    token = ws.request_headers.get("x-device-token")

    logger.info(f"New connection from {ws.remote_address}, path: {path}")

    if not device_id or not token:
        logger.warning(f"Missing credentials from {ws.remote_address}")
        await ws.send(json.dumps({"type": "error", "message": "missing device_id/token"}))
        await ws.close(code=4401, reason="missing credentials")
        return

    if not registry.is_valid(device_id, token):
        logger.warning(f"Invalid token for device {device_id}")
        await ws.send(json.dumps({"type": "error", "message": "invalid token"}))
        await ws.close(code=4401, reason="invalid token")
        return

    state = ConnState(device_id)
    logger.info(f"[{state.session_id}] Device {device_id} authenticated, session started")

    try:
        async for message in ws:
            if isinstance(message, str):
                await handle_text_message(ws, state, message)
            elif isinstance(message, bytes):
                await handle_binary_message(ws, state, message)
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"[{state.session_id}] Device {device_id} disconnected")
    except Exception as e:
        logger.error(f"[{state.session_id}] Error handling device {device_id}: {e}", exc_info=True)
    finally:
        # Clean abort on disconnect
        state.tts_abort = True
        logger.info(f"[{state.session_id}] Session ended for device {device_id}")


async def start_websocket_server():
    """Start the WebSocket server on configured port"""
    logger.info(f"Starting WebSocket server on {settings.ws_host}:{settings.ws_port}")

    async with websockets.serve(
        handle_client,
        settings.ws_host,
        settings.ws_port,
        ping_interval=20,
        ping_timeout=60,
    ):
        logger.info(f"WebSocket server listening on ws://{settings.ws_host}:{settings.ws_port}/ws")
        await asyncio.Future()  # Run forever
