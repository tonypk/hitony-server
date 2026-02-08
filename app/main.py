import asyncio
import base64
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from .config import settings
from .protocol import AsrText, TtsStart, TtsEnd, ErrorMsg
from .registry import registry
from .asr import transcribe_pcm
from .tts import synthesize_tts
from .openclaw import call_openclaw

app = FastAPI()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OPENCLAW_CFG_PATH = os.path.join(DATA_DIR, "openclaw.json")

@app.on_event("startup")
async def load_openclaw_config():
    if os.path.exists(OPENCLAW_CFG_PATH):
        try:
            with open(OPENCLAW_CFG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings.openclaw_url = data.get("openclaw_url", settings.openclaw_url)
            settings.openclaw_token = data.get("openclaw_token", settings.openclaw_token)
            settings.openclaw_model = data.get("openclaw_model", settings.openclaw_model)
        except Exception:
            pass

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

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>EchoEar Admin</title>
    <style>
      body { font-family: sans-serif; max-width: 760px; margin: 24px auto; padding: 0 16px; }
      h2 { margin-top: 24px; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; }
      input { padding: 6px; margin: 4px 0; width: 100%; }
      button { padding: 6px 10px; margin-top: 6px; }
      .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .danger { background: #b30000; color: white; border: none; }
    </style>
  </head>
  <body>
    <h1>EchoEar Admin</h1>

    <h2>Devices</h2>
    <div class="row">
      <div>
        <label>Device ID</label>
        <input id="device_id" placeholder="echoear-001" />
      </div>
      <div>
        <label>Token</label>
        <input id="device_token" placeholder="devtoken" />
      </div>
    </div>
    <button onclick="addDevice()">Add/Update</button>

    <table id="device_table">
      <thead><tr><th>Device ID</th><th>Token</th><th>Action</th></tr></thead>
      <tbody></tbody>
    </table>

    <h2>OpenClaw Config</h2>
    <label>OpenClaw URL</label>
    <input id="openclaw_url" />
    <label>OpenClaw Token</label>
    <input id="openclaw_token" />
    <label>OpenClaw Model</label>
    <input id="openclaw_model" />
    <button onclick="saveOpenclaw()">Save OpenClaw</button>

    <script>
      async function loadDevices() {
        const res = await fetch('/admin/devices');
        const data = await res.json();
        const tbody = document.querySelector('#device_table tbody');
        tbody.innerHTML = '';
        Object.entries(data.devices).forEach(([id, token]) => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${id}</td><td>${token}</td>
            <td><button class="danger" onclick="delDevice('${id}')">Delete</button></td>`;
          tbody.appendChild(tr);
        });
      }

      async function addDevice() {
        const device_id = document.getElementById('device_id').value.trim();
        const token = document.getElementById('device_token').value.trim();
        if (!device_id || !token) return alert('device_id/token required');
        await fetch('/admin/devices', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({device_id, token})
        });
        await loadDevices();
      }

      async function delDevice(id) {
        await fetch('/admin/devices/' + encodeURIComponent(id), {method: 'DELETE'});
        await loadDevices();
      }

      async function loadOpenclaw() {
        const res = await fetch('/admin/openclaw');
        const data = await res.json();
        document.getElementById('openclaw_url').value = data.openclaw_url || '';
        document.getElementById('openclaw_token').value = data.openclaw_token || '';
        document.getElementById('openclaw_model').value = data.openclaw_model || '';
      }

      async function saveOpenclaw() {
        const openclaw_url = document.getElementById('openclaw_url').value.trim();
        const openclaw_token = document.getElementById('openclaw_token').value.trim();
        const openclaw_model = document.getElementById('openclaw_model').value.trim();
        await fetch('/admin/openclaw', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({openclaw_url, openclaw_token, openclaw_model})
        });
        alert('Saved');
      }

      loadDevices();
      loadOpenclaw();
    </script>
  </body>
</html>
"""

@app.get("/admin/devices")
async def admin_list_devices():
    return {"devices": registry.list_devices()}

@app.post("/admin/devices")
async def admin_add_device(payload: dict):
    device_id = payload.get("device_id")
    token = payload.get("token")
    if not device_id or not token:
        raise HTTPException(status_code=400, detail="device_id and token required")
    registry.register(device_id, token)
    return {"ok": True}

@app.delete("/admin/devices/{device_id}")
async def admin_delete_device(device_id: str):
    if not registry.delete(device_id):
        raise HTTPException(status_code=404, detail="device_id not found")
    return {"ok": True}

@app.get("/admin/openclaw")
async def admin_get_openclaw():
    return {
        "openclaw_url": settings.openclaw_url,
        "openclaw_token": settings.openclaw_token,
        "openclaw_model": settings.openclaw_model,
    }

@app.post("/admin/openclaw")
async def admin_set_openclaw(payload: dict):
    openclaw_url = payload.get("openclaw_url", settings.openclaw_url)
    openclaw_token = payload.get("openclaw_token", settings.openclaw_token)
    openclaw_model = payload.get("openclaw_model", settings.openclaw_model)
    settings.openclaw_url = openclaw_url
    settings.openclaw_token = openclaw_token
    settings.openclaw_model = openclaw_model
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OPENCLAW_CFG_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "openclaw_url": settings.openclaw_url,
            "openclaw_token": settings.openclaw_token,
            "openclaw_model": settings.openclaw_model,
        }, f, ensure_ascii=False, indent=2)
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
