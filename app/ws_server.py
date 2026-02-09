"""WebSocket server using websockets library (xiaozhi-compatible)"""
import asyncio
import json
import logging
from typing import Optional
import websockets
from websockets.server import WebSocketServerProtocol

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
        self.audio_buf = bytearray()
        self.listening = False

async def handle_text_message(ws: WebSocketServerProtocol, state: ConnState, text: str):
    """Handle text JSON messages from device"""
    try:
        payload = json.loads(text)
    except Exception:
        await ws.send(json.dumps({"type": "error", "message": "invalid json"}))
        return

    mtype = payload.get("type")
    logger.info(f"Device {state.device_id} text message: {mtype}")

    if mtype == "hello":
        # Acknowledge hello
        return
    elif mtype == "wake":
        # Device woke up
        return
    elif mtype == "audio_start":
        state.audio_buf = bytearray()
        state.listening = True
        return
    elif mtype == "audio_end":
        state.listening = False
        await process_audio(ws, state)
        return

async def handle_binary_message(ws: WebSocketServerProtocol, state: ConnState, chunk: bytes):
    """Accumulate audio chunks"""
    if not state.listening:
        return
    state.audio_buf.extend(chunk)

async def process_audio(ws: WebSocketServerProtocol, state: ConnState):
    """Process accumulated audio: ASR -> LLM -> TTS"""
    pcm = bytes(state.audio_buf)
    if not pcm:
        await ws.send(json.dumps({"type": "error", "message": "empty audio"}))
        return

    # ASR
    try:
        text = await transcribe_pcm(pcm)
        logger.info(f"ASR result: {text}")
    except Exception as e:
        logger.error(f"ASR failed: {e}")
        await ws.send(json.dumps({"type": "error", "message": f"ASR failed: {e}"}))
        return

    await ws.send(json.dumps({"type": "asr_text", "text": text}))

    # LLM via OpenClaw (with fallback for testing)
    try:
        reply = await call_openclaw(text)
        logger.info(f"LLM reply: {reply}")
    except Exception as e:
        logger.warning(f"OpenClaw failed: {e}, using test response")
        # Fallback: return a test response (English for better TTS quality)
        reply = "OK, I got it"

    # TTS
    try:
        audio = await synthesize_tts(reply)
        logger.info(f"TTS synthesized {len(audio)} bytes")
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        await ws.send(json.dumps({"type": "error", "message": f"TTS failed: {e}"}))
        return

    await ws.send(json.dumps({"type": "tts_start"}))

    # Stream PCM in larger chunks (60ms @ 16kHz = 1920 bytes)
    # Larger chunks reduce network overhead and improve stability
    chunk_size = 1920  # 60ms frames like xiaozhi
    for i in range(0, len(audio), chunk_size):
        await ws.send(audio[i:i+chunk_size])
        await asyncio.sleep(0.02)  # Small delay to pace transmission

    await ws.send(json.dumps({"type": "tts_end"}))

async def handle_client(ws: WebSocketServerProtocol, path: str):
    """Main WebSocket connection handler"""
    # Extract device_id and token from headers
    device_id = ws.request_headers.get("x-device-id")
    token = ws.request_headers.get("x-device-token")

    # Log all headers for debugging
    logger.info(f"New connection from {ws.remote_address}")
    logger.info(f"Path: {path}")
    logger.info(f"Headers: {dict(ws.request_headers)}")
    logger.info(f"Extracted: device_id={device_id}, token={token}")

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

    logger.info(f"Device {device_id} authenticated successfully")
    state = ConnState(device_id)

    try:
        # Main message loop
        async for message in ws:
            if isinstance(message, str):
                await handle_text_message(ws, state, message)
            elif isinstance(message, bytes):
                await handle_binary_message(ws, state, message)
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Device {device_id} disconnected")
    except Exception as e:
        logger.error(f"Error handling device {device_id}: {e}", exc_info=True)
    finally:
        logger.info(f"Connection closed for device {device_id}")

async def start_websocket_server():
    """Start the WebSocket server on configured port"""
    logger.info(f"Starting WebSocket server on {settings.ws_host}:{settings.ws_port}")

    # websockets library automatically handles ping/pong with default 20s interval
    async with websockets.serve(
        handle_client,
        settings.ws_host,
        settings.ws_port,
        ping_interval=20,  # Send ping every 20 seconds
        ping_timeout=60,   # Wait up to 60 seconds for pong
    ):
        logger.info(f"WebSocket server listening on ws://{settings.ws_host}:{settings.ws_port}/ws")
        await asyncio.Future()  # Run forever
