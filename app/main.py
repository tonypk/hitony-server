import asyncio
import base64
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import JSONResponse
from .config import settings
from .protocol import AsrText, TtsStart, TtsEnd, ErrorMsg
from .registry import registry
from .asr import transcribe_pcm
from .tts import synthesize_tts
from .openclaw import call_openclaw

app = FastAPI()

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/register")
async def register_device(payload: dict):
    device_id = payload.get("device_id")
    token = payload.get("token")
    if not device_id or not token:
        raise HTTPException(status_code=400, detail="device_id and token required")
    registry.register(device_id, token)
    return {"ok": True}

@app.get("/ota/")
async def ota(request: Request):
    # Return minimal OTA config for devices
    host = request.headers.get("host", f"{settings.ws_host}:{settings.ws_port}")
    return {
        "websocket": {
            "url": f"ws://{host}/ws",
            "version": 3
        }
    }

class ConnState:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.audio_buf = bytearray()
        self.listening = False

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    device_id = ws.query_params.get("device_id") or ws.headers.get("x-device-id")
    token = ws.query_params.get("token") or ws.headers.get("x-device-token")

    if not device_id or not token:
        await ws.send_json(ErrorMsg(type="error", message="missing device_id/token").dict())
        await ws.close(code=4401)
        return

    if not registry.is_valid(device_id, token):
        await ws.send_json(ErrorMsg(type="error", message="invalid token").dict())
        await ws.close(code=4401)
        return

    state = ConnState(device_id)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if "text" in msg and msg["text"]:
                data = msg["text"]
                await handle_text_message(ws, state, data)
            elif "bytes" in msg and msg["bytes"]:
                await handle_binary_message(ws, state, msg["bytes"])
    except WebSocketDisconnect:
        pass

async def handle_text_message(ws: WebSocket, state: ConnState, text: str):
    # Expect simple JSON messages
    import json
    try:
        payload = json.loads(text)
    except Exception:
        await ws.send_json(ErrorMsg(type="error", message="invalid json").dict())
        return

    mtype = payload.get("type")
    if mtype == "hello":
        # No-op, can respond if needed
        return
    if mtype == "wake":
        # Device woke up
        return
    if mtype == "audio_start":
        state.audio_buf = bytearray()
        state.listening = True
        return
    if mtype == "audio_end":
        state.listening = False
        await process_audio(ws, state)
        return

async def handle_binary_message(ws: WebSocket, state: ConnState, chunk: bytes):
    if not state.listening:
        return
    state.audio_buf.extend(chunk)

async def process_audio(ws: WebSocket, state: ConnState):
    pcm = bytes(state.audio_buf)
    if not pcm:
        await ws.send_json(ErrorMsg(type="error", message="empty audio").dict())
        return

    # ASR
    try:
        text = await transcribe_pcm(pcm)
    except Exception as e:
        await ws.send_json(ErrorMsg(type="error", message=f"ASR failed: {e}").dict())
        return

    await ws.send_json(AsrText(type="asr_text", text=text).dict())

    # LLM via OpenClaw
    try:
        reply = await call_openclaw(text)
    except Exception as e:
        await ws.send_json(ErrorMsg(type="error", message=f"OpenClaw failed: {e}").dict())
        return

    # TTS
    try:
        audio = await synthesize_tts(reply)
    except Exception as e:
        await ws.send_json(ErrorMsg(type="error", message=f"TTS failed: {e}").dict())
        return

    await ws.send_json(TtsStart(type="tts_start").dict())
    # Chunk PCM for streaming playback
    chunk_size = 640  # 20ms @ 16kHz mono 16-bit = 640 bytes
    for i in range(0, len(audio), chunk_size):
        await ws.send_bytes(audio[i:i+chunk_size])
        await asyncio.sleep(0)  # yield
    await ws.send_json(TtsEnd(type="tts_end").dict())
