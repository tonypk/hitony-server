"""
HiTony Server - FastAPI HTTP endpoints + admin dashboard.
WebSocket server runs separately via ws_server.py (launched by run_server.py).
"""
import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import settings
from .api import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()
app.include_router(api_router)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OPENAI_CFG_PATH = os.path.join(DATA_DIR, "openai.json")


@app.on_event("startup")
async def on_startup():
    """Initialize database and load legacy config."""
    from .database import init_db
    await init_db()

    # Start meeting cleanup background task
    asyncio.create_task(_meeting_cleanup_loop())

    # Load legacy openai.json config (backward compat)
    if os.path.exists(OPENAI_CFG_PATH):
        try:
            with open(OPENAI_CFG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings.openai_base_url = data.get("openai_base_url", settings.openai_base_url)
            settings.openai_chat_model = data.get("openai_chat_model", settings.openai_chat_model)
            settings.openai_tts_model = data.get("openai_tts_model", settings.openai_tts_model)
            settings.openai_tts_voice = data.get("openai_tts_voice", settings.openai_tts_voice)
            logger.info("Loaded legacy OpenAI config from disk")
        except Exception as e:
            logger.warning(f"Failed to load OpenAI config: {e}")


async def _meeting_cleanup_loop():
    """Periodically delete meeting recordings older than retention period."""
    while True:
        await asyncio.sleep(3600)  # Run every hour
        try:
            retention_days = settings.meeting_retention_days
            if retention_days <= 0:
                continue
            cutoff = datetime.utcnow() - timedelta(days=retention_days)

            from .database import async_session_factory
            from .models import Meeting
            from sqlalchemy import select

            data_dir = Path(DATA_DIR)
            async with async_session_factory() as db:
                result = await db.execute(
                    select(Meeting).where(Meeting.created_at < cutoff)
                )
                old_meetings = result.scalars().all()
                for m in old_meetings:
                    if m.audio_path:
                        fp = data_dir / m.audio_path
                        if fp.exists():
                            fp.unlink()
                            logger.info(f"Cleanup: deleted audio {fp}")
                    await db.delete(m)
                if old_meetings:
                    await db.commit()
                    logger.info(f"Cleanup: removed {len(old_meetings)} meetings older than {retention_days} days")
        except Exception as e:
            logger.error(f"Meeting cleanup error: {e}")


# ── Legacy endpoints (backward compat) ───────────────────────

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/register")
async def register_device(payload: dict):
    device_id = payload.get("device_id")
    token = payload.get("token")
    if not device_id or not token:
        raise HTTPException(status_code=400, detail="device_id and token required")

    from .database import async_session_factory
    from .models import Device
    from .auth import hash_token
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(select(Device).where(Device.device_id == device_id))
        device = result.scalar_one_or_none()
        if device:
            device.token_hash = hash_token(token)
        else:
            device = Device(device_id=device_id, token_hash=hash_token(token))
            db.add(device)
        await db.commit()

    logger.info(f"Registered device: {device_id} (DB)")
    return {"ok": True}

@app.get("/ota/")
async def ota(request: Request):
    host = request.headers.get("host", f"{settings.ws_host}:{settings.ws_port}")
    return {"websocket": {"url": f"ws://{host}/ws", "version": 3}}


# ── OTA firmware upload ───────────────────────────────────────

from fastapi import UploadFile, File, Form, Depends
from .auth import get_current_user
from pathlib import Path

OTA_DIR = Path(DATA_DIR) / "ota"


@app.post("/api/ota/upload-form")
async def ota_upload_form(
    file: UploadFile = File(...),
    version: str = Form(...),
    user = Depends(get_current_user),
):
    """Upload a new firmware binary (multipart form). Requires authentication."""

    os.makedirs(OTA_DIR, exist_ok=True)

    filename = f"hitony_{version}.bin"
    fw_path = OTA_DIR / filename

    content = await file.read()
    with open(fw_path, "wb") as f:
        f.write(content)

    # OTA URL — devices download from public-facing nginx on port 80
    ota_url = os.getenv("HITONY_OTA_URL", "http://136.111.249.161/api/ota/firmware")

    # Write metadata
    import json as _json
    meta = {
        "version": version,
        "filename": filename,
        "size": len(content),
        "url": ota_url,
        "uploaded_at": datetime.now().isoformat(),
    }
    with open(OTA_DIR / "latest.json", "w") as f:
        _json.dump(meta, f)

    logger.info(f"OTA firmware uploaded: {filename} ({len(content)} bytes, version={version})")
    return {"ok": True, "version": version, "size": len(content)}


# ── Admin dashboard ───────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return _ADMIN_HTML


_ADMIN_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>HiTony Admin</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: #f5f5f5; color: #333; }
    .container { max-width: 640px; margin: 0 auto; padding: 16px; }
    h1 { text-align: center; padding: 20px 0 8px; font-size: 1.5em; }
    .subtitle { text-align: center; color: #888; font-size: 0.9em; margin-bottom: 20px; }

    /* Tabs */
    .tabs { display: flex; border-bottom: 2px solid #ddd; margin-bottom: 16px; }
    .tab { padding: 10px 20px; cursor: pointer; border-bottom: 2px solid transparent;
           margin-bottom: -2px; font-weight: 500; color: #666; }
    .tab.active { color: #2563eb; border-bottom-color: #2563eb; }

    /* Cards */
    .card { background: white; border-radius: 8px; padding: 16px; margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .card h3 { margin-bottom: 12px; font-size: 1.1em; }

    /* Forms */
    label { display: block; font-size: 0.85em; color: #555; margin: 8px 0 4px; font-weight: 500; }
    input { width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px;
            font-size: 0.95em; }
    input:focus { outline: none; border-color: #2563eb; }
    .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

    /* Buttons */
    .btn { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer;
           font-size: 0.9em; font-weight: 500; }
    .btn-primary { background: #2563eb; color: white; }
    .btn-danger { background: #dc2626; color: white; }
    .btn-sm { padding: 4px 10px; font-size: 0.8em; }
    .btn:hover { opacity: 0.9; }

    /* Table */
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px; text-align: left; border-bottom: 1px solid #eee; font-size: 0.9em; }
    th { color: #888; font-weight: 500; }

    /* Messages */
    .msg { padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 0.9em; display: none; }
    .msg-ok { background: #dcfce7; color: #166534; }
    .msg-err { background: #fee2e2; color: #991b1b; }

    /* Auth page */
    #auth-page { max-width: 360px; margin: 60px auto; }
    #auth-page h2 { margin-bottom: 16px; }

    .hidden { display: none; }
    .mt { margin-top: 12px; }
    .text-center { text-align: center; }
    .text-sm { font-size: 0.85em; color: #666; }
    a { color: #2563eb; cursor: pointer; }

    /* Chat bubbles */
    .chat-msg { margin: 6px 0; display: flex; }
    .chat-msg.user { justify-content: flex-end; }
    .chat-msg .bubble { max-width: 80%; padding: 8px 12px; border-radius: 12px; font-size: 0.9em; line-height: 1.4; word-break: break-word; }
    .chat-msg.user .bubble { background: #2563eb; color: white; border-bottom-right-radius: 4px; }
    .chat-msg.assistant .bubble { background: #e5e7eb; color: #333; border-bottom-left-radius: 4px; }
    .chat-role { font-size: 0.7em; color: #999; margin-bottom: 2px; }
  </style>
</head>
<body>

<!-- AUTH PAGE -->
<div id="auth-page" class="container">
  <h1>HiTony</h1>
  <p class="subtitle">Voice Assistant Admin</p>
  <div class="card">
    <h3 id="auth-title">Login</h3>
    <div id="auth-msg" class="msg"></div>
    <label>Email</label>
    <input id="auth-email" type="email" placeholder="you@example.com"/>
    <label>Password</label>
    <input id="auth-pass" type="password" placeholder="password"/>
    <button class="btn btn-primary mt" style="width:100%" onclick="doAuth()">Login</button>
    <p class="text-center mt text-sm">
      <span id="auth-toggle-text">No account?</span>
      <a id="auth-toggle" onclick="toggleAuth()">Register</a>
    </p>
  </div>
</div>

<!-- MAIN APP -->
<div id="app-page" class="container hidden">
  <h1>HiTony</h1>
  <p class="subtitle" id="user-email"></p>

  <div class="tabs">
    <div class="tab active" onclick="showTab('devices')">Devices</div>
    <div class="tab" onclick="showTab('history')">History</div>
    <div class="tab" onclick="showTab('meetings')">Meetings</div>
    <div class="tab" onclick="showTab('reminders')">Reminders</div>
    <div class="tab" onclick="showTab('settings')">Settings</div>
    <div class="tab" onclick="logout()">Logout</div>
  </div>

  <!-- DEVICES TAB -->
  <div id="tab-devices">
    <div class="card">
      <h3>Add Device</h3>
      <div class="row2">
        <div><label>Device ID</label><input id="d-id" placeholder="hitony-001"/></div>
        <div><label>Token</label><input id="d-token" placeholder="devtoken"/></div>
      </div>
      <label>Name (optional)</label>
      <input id="d-name" placeholder="My HiTony"/>
      <button class="btn btn-primary mt" onclick="addDevice()">Add Device</button>
    </div>
    <div class="card">
      <h3>My Devices</h3>
      <div id="dev-msg" class="msg"></div>
      <table>
        <thead><tr><th>Device ID</th><th>Name</th><th>Status</th><th>Last Seen</th><th></th></tr></thead>
        <tbody id="dev-tbody"></tbody>
      </table>
      <p id="dev-empty" class="text-sm mt text-center hidden">No devices yet</p>
    </div>
    <div class="card" id="stats-card" style="display:none">
      <h3>Usage Overview</h3>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;text-align:center">
        <div><div style="font-size:1.5em;font-weight:600" id="stat-meetings">-</div><div class="text-sm">Meetings</div></div>
        <div><div style="font-size:1.5em;font-weight:600" id="stat-reminders">-</div><div class="text-sm">Reminders</div></div>
        <div><div style="font-size:1.5em;font-weight:600" id="stat-messages">-</div><div class="text-sm">Messages</div></div>
      </div>
      <div id="stat-details" class="text-sm mt" style="color:#666"></div>
    </div>
  </div>

  <!-- HISTORY TAB -->
  <div id="tab-history" class="hidden">
    <div class="card">
      <h3>Conversation History</h3>
      <div style="margin-bottom:12px">
        <label>Select Device</label>
        <select id="hist-device" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:0.95em" onchange="loadConversation()">
          <option value="">-- Select device --</option>
        </select>
      </div>
      <div id="hist-msg" class="msg"></div>
      <div id="hist-chat" style="max-height:400px;overflow-y:auto;border:1px solid #eee;border-radius:6px;padding:8px;display:none">
      </div>
      <p id="hist-empty" class="text-sm mt text-center hidden">No conversation history</p>
      <button id="hist-clear-btn" class="btn btn-danger btn-sm mt" style="display:none" onclick="clearConversation()">Clear History</button>
    </div>
  </div>

  <!-- MEETINGS TAB -->
  <div id="tab-meetings" class="hidden">
    <div class="card">
      <h3>Meeting Recordings</h3>
      <div id="mtg-msg" class="msg"></div>
      <table>
        <thead><tr><th>Title</th><th>Duration</th><th>Status</th><th>Date</th><th></th></tr></thead>
        <tbody id="mtg-tbody"></tbody>
      </table>
      <p id="mtg-empty" class="text-sm mt text-center hidden">No meeting recordings yet. Say "start meeting" to your HiTony device!</p>
    </div>
  </div>

  <!-- REMINDERS TAB -->
  <div id="tab-reminders" class="hidden">
    <div class="card">
      <h3>My Reminders</h3>
      <div id="rem-msg" class="msg"></div>
      <table>
        <thead><tr><th>Time</th><th>Message</th><th>Status</th><th></th></tr></thead>
        <tbody id="rem-tbody"></tbody>
      </table>
      <p id="rem-empty" class="text-sm mt text-center hidden">No reminders yet. Say "remind me..." to your HiTony device!</p>
    </div>
  </div>

  <!-- SETTINGS TAB -->
  <div id="tab-settings" class="hidden">
    <div class="card">
      <h3>LLM Provider</h3>
      <div id="set-msg" class="msg"></div>
      <label>Quick Setup</label>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 12px">
        <button class="btn btn-sm" style="background:#10a37f;color:#fff" onclick="setProvider('openai')">OpenAI</button>
        <button class="btn btn-sm" style="background:#4e6ef2;color:#fff" onclick="setProvider('deepseek')">DeepSeek</button>
        <button class="btn btn-sm" style="background:#f55036;color:#fff" onclick="setProvider('groq')">Groq</button>
        <button class="btn btn-sm" style="background:#7c3aed;color:#fff" onclick="setProvider('openrouter')">OpenRouter</button>
        <button class="btn btn-sm" style="background:#333;color:#fff" onclick="setProvider('ollama')">Ollama (Local)</button>
      </div>
      <label>API Key</label>
      <input id="s-apikey" type="password" placeholder="sk-..."/>
      <p class="text-sm" id="s-apikey-status"></p>
      <label>Base URL</label>
      <input id="s-baseurl" placeholder="https://api.openai.com/v1"/>
      <div class="row2">
        <div><label>Chat Model</label><input id="s-chat" placeholder="gpt-4o-mini"/></div>
        <div><label>ASR Model</label><input id="s-asr" placeholder="whisper-1"/></div>
      </div>
    </div>
    <div class="card">
      <h3>TTS (Text-to-Speech)</h3>
      <label>TTS Provider</label>
      <select id="s-tts-provider" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:0.95em;margin-bottom:8px" onchange="onTtsProviderChange()">
        <option value="">OpenAI TTS (requires API key)</option>
        <option value="edge">Edge TTS (free, no key needed)</option>
      </select>
      <div id="tts-openai-fields">
        <div class="row2">
          <div><label>TTS Model</label><input id="s-tts" placeholder="tts-1"/></div>
          <div><label>TTS Voice</label><input id="s-voice" placeholder="alloy"/></div>
        </div>
      </div>
      <div id="tts-edge-fields" class="hidden">
        <label>Voice</label>
        <select id="s-edge-voice" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:0.95em">
          <option value="xiaoxiao">Xiaoxiao (Chinese female, warm)</option>
          <option value="yunxi">Yunxi (Chinese male)</option>
          <option value="xiaoyi">Xiaoyi (Chinese female, lively)</option>
          <option value="jenny">Jenny (English female)</option>
          <option value="guy">Guy (English male)</option>
          <option value="aria">Aria (English female, natural)</option>
        </select>
        <p class="text-sm" style="margin-top:4px;color:#16a34a">No API key required for Edge TTS</p>
      </div>
    </div>
    <div class="card">
      <h3>Tool API Keys</h3>
      <p class="text-sm" style="margin-bottom:8px">Configure API keys for weather and web search tools. Each service has a free tier.</p>
      <label>OpenWeatherMap API Key <a href="https://openweathermap.org/api" target="_blank" style="font-size:0.8em">(free signup)</a></label>
      <input id="s-weather-key" type="password" placeholder="your-weather-api-key"/>
      <p class="text-sm" id="s-weather-key-status"></p>
      <label>Default City</label>
      <input id="s-weather-city" placeholder="Singapore"/>
      <label style="margin-top:12px">Tavily API Key <a href="https://tavily.com" target="_blank" style="font-size:0.8em">(1000 free/month)</a></label>
      <input id="s-tavily-key" type="password" placeholder="tvly-..."/>
      <p class="text-sm" id="s-tavily-key-status"></p>
      <label style="margin-top:12px">YouTube Data API Key <a href="https://console.cloud.google.com/apis/credentials" target="_blank" style="font-size:0.8em">(Google Cloud Console)</a></label>
      <input id="s-youtube-key" type="password" placeholder="AIza..."/>
      <p class="text-sm" id="s-youtube-key-status"></p>
      <p class="text-sm" style="color:#666">Used for music search. Free quota: ~100 searches/day. Without key, falls back to yt-dlp search.</p>
    </div>
    <div class="card">
      <h3>Notion Integration</h3>
      <p class="text-sm" style="margin-bottom:8px">Connect to Notion for voice notes and meeting transcripts. <a href="https://www.notion.so/profile/integrations" target="_blank">Create integration</a> &rarr; copy token.</p>
      <label>Integration Token</label>
      <input id="s-notion-token" type="password" placeholder="ntn_..."/>
      <p class="text-sm" id="s-notion-token-status"></p>
      <label>Database ID <span style="color:#999;font-weight:normal">(optional - will auto-create)</span> <a href="https://developers.notion.com/docs/working-with-databases#adding-pages-to-a-database" target="_blank" style="font-size:0.8em">(how to find)</a></label>
      <input id="s-notion-dbid" placeholder="Leave empty to auto-create 'HiTony Meetings' database"/>
      <p class="text-sm" style="color:#16a34a">✨ <strong>New!</strong> Leave Database ID empty - we'll automatically create "HiTony Meetings" database on first use and save the ID for you.</p>
      <button class="btn btn-sm mt" style="background:#000;color:#fff" onclick="testNotion()">Test Connection</button>
      <span id="notion-test-result" class="text-sm" style="margin-left:8px"></span>
    </div>
    <button class="btn btn-primary mt" style="width:100%" onclick="saveSettings()">Save Settings</button>
    <div class="card" style="margin-top:16px">
      <h3>OTA Firmware Update</h3>
      <p class="text-sm" style="margin-bottom:8px">Upload a new firmware binary and push it to connected devices.</p>
      <div id="ota-msg" class="msg"></div>
      <div id="ota-current" class="text-sm" style="margin-bottom:8px"></div>
      <label>Firmware Version</label>
      <input id="ota-version" placeholder="e.g. 2.1.0"/>
      <label>Firmware Binary (.bin)</label>
      <input id="ota-file" type="file" accept=".bin" style="margin:4px 0"/>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn btn-primary" onclick="uploadFirmware()">Upload</button>
        <button class="btn btn-sm" style="background:#059669;color:#fff" onclick="pushOTA()">Push to Devices</button>
      </div>
    </div>
  </div>
</div>

<script>
let TOKEN = localStorage.getItem('hitony_token');
let IS_REGISTER = false;

// ── Auth ──
function toggleAuth() {
  IS_REGISTER = !IS_REGISTER;
  document.getElementById('auth-title').textContent = IS_REGISTER ? 'Register' : 'Login';
  document.getElementById('auth-toggle-text').textContent = IS_REGISTER ? 'Have an account?' : 'No account?';
  document.getElementById('auth-toggle').textContent = IS_REGISTER ? 'Login' : 'Register';
}

async function doAuth() {
  const email = document.getElementById('auth-email').value.trim();
  const pass = document.getElementById('auth-pass').value;
  if (!email || !pass) return showMsg('auth-msg', 'Email and password required', true);
  const endpoint = IS_REGISTER ? '/api/auth/register' : '/api/auth/login';
  try {
    const res = await fetch(endpoint, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({email, password: pass})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Auth failed');
    TOKEN = data.access_token;
    localStorage.setItem('hitony_token', TOKEN);
    localStorage.setItem('hitony_email', data.email);
    enterApp();
  } catch(e) { showMsg('auth-msg', e.message, true); }
}

function logout() {
  TOKEN = null;
  localStorage.removeItem('hitony_token');
  localStorage.removeItem('hitony_email');
  document.getElementById('auth-page').classList.remove('hidden');
  document.getElementById('app-page').classList.add('hidden');
}

function enterApp() {
  document.getElementById('auth-page').classList.add('hidden');
  document.getElementById('app-page').classList.remove('hidden');
  document.getElementById('user-email').textContent = localStorage.getItem('hitony_email') || '';
  loadDevices();
  loadReminders();
  loadSettings();
  loadStats();
}

// ── HTML escape helper (XSS prevention) ──
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// ── API helper ──
async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Authorization': 'Bearer ' + TOKEN} };
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (res.status === 401) { logout(); throw new Error('Session expired'); }
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

// ── Devices ──
async function loadDevices() {
  try {
    const [devices, statuses] = await Promise.all([
      api('/api/devices'),
      api('/api/devices/status').catch(() => [])
    ]);
    const statusMap = {};
    statuses.forEach(s => { statusMap[s.device_id] = s; });
    const tbody = document.getElementById('dev-tbody');
    const empty = document.getElementById('dev-empty');
    tbody.innerHTML = '';
    if (devices.length === 0) { empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    devices.forEach(d => {
      const seen = d.last_seen ? new Date(d.last_seen).toLocaleString() : 'Never';
      const st = statusMap[d.device_id];
      const online = st && st.online;
      let badge = online
        ? '<span style="color:#4caf50;font-weight:600">● Online</span>'
        : '<span style="color:#999">○ Offline</span>';
      if (online && st.music_playing) badge += ' <span style="color:#ff9800;font-size:0.8em">♪</span>';
      if (online && st.meeting_active) badge += ' <span style="color:#f44336;font-size:0.8em">●REC</span>';
      const fw = (st && st.fw_version) ? ` <span style="color:#888;font-size:0.8em">v${esc(st.fw_version)}</span>` : '';
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${esc(d.device_id)}${fw}</td><td>${esc(d.name||'-')}</td><td>${badge}</td><td>${esc(seen)}</td>
        <td><button class="btn btn-danger btn-sm" onclick="delDevice('${esc(d.device_id)}')">Remove</button></td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { showMsg('dev-msg', e.message, true); }
}

async function addDevice() {
  const device_id = document.getElementById('d-id').value.trim();
  const token = document.getElementById('d-token').value.trim();
  const name = document.getElementById('d-name').value.trim();
  if (!device_id || !token) return showMsg('dev-msg', 'Device ID and token required', true);
  try {
    await api('/api/devices', 'POST', {device_id, token, name});
    document.getElementById('d-id').value = '';
    document.getElementById('d-token').value = '';
    document.getElementById('d-name').value = '';
    showMsg('dev-msg', 'Device added', false);
    await loadDevices();
  } catch(e) { showMsg('dev-msg', e.message, true); }
}

async function delDevice(id) {
  if (!confirm('Remove device ' + id + '?')) return;
  try {
    await api('/api/devices/' + encodeURIComponent(id), 'DELETE');
    showMsg('dev-msg', 'Device removed', false);
    await loadDevices();
  } catch(e) { showMsg('dev-msg', e.message, true); }
}

// ── Reminders ──
async function loadReminders() {
  try {
    const reminders = await api('/api/reminders');
    const tbody = document.getElementById('rem-tbody');
    const empty = document.getElementById('rem-empty');
    tbody.innerHTML = '';
    if (reminders.length === 0) { empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    reminders.forEach(r => {
      const dt = new Date(r.remind_at).toLocaleString();
      const status = r.delivered === 0 ? 'Pending' : r.delivered === 1 ? 'Delivered' : 'Failed';
      const statusCls = r.delivered === 0 ? 'color:#d97706' : r.delivered === 1 ? 'color:#16a34a' : 'color:#dc2626';
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${esc(dt)}</td><td>${esc(r.message)}</td><td style="${statusCls}">${status}</td>
        <td><button class="btn btn-danger btn-sm" onclick="delReminder(${r.id})">Delete</button></td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { showMsg('rem-msg', e.message, true); }
}

async function delReminder(id) {
  if (!confirm('Delete this reminder?')) return;
  try {
    await api('/api/reminders/' + id, 'DELETE');
    showMsg('rem-msg', 'Reminder deleted', false);
    await loadReminders();
  } catch(e) { showMsg('rem-msg', e.message, true); }
}

// ── Meetings ──
async function loadMeetings() {
  try {
    const meetings = await api('/api/meetings');
    const tbody = document.getElementById('mtg-tbody');
    const empty = document.getElementById('mtg-empty');
    tbody.innerHTML = '';
    if (meetings.length === 0) { empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    meetings.forEach(m => {
      const dt = m.started_at ? new Date(m.started_at).toLocaleString() : '-';
      const dur = m.duration_s > 0 ? Math.floor(m.duration_s/60) + ':' + String(m.duration_s%60).padStart(2,'0') : '-';
      const statusMap = {recording:'Recording',ended:'Ended',transcribed:'Transcribed'};
      const statusText = statusMap[m.status] || m.status;
      const statusCls = m.status==='recording'?'color:#d97706':m.status==='transcribed'?'color:#16a34a':'color:#555';
      const dlBtn = m.status !== 'recording' ? `<button class="btn btn-sm" style="background:#2563eb;color:#fff;margin-right:4px" onclick="downloadMeeting(${m.id})">Download</button>` : '';
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${esc(m.title)}</td><td>${esc(dur)}</td><td style="${statusCls}">${esc(statusText)}</td><td>${esc(dt)}</td>
        <td>${dlBtn}<button class="btn btn-danger btn-sm" onclick="delMeeting(${m.id})">Delete</button></td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { showMsg('mtg-msg', e.message, true); }
}

function downloadMeeting(id) {
  window.open('/api/meetings/' + id + '/download?token=' + encodeURIComponent(TOKEN), '_blank');
}

async function delMeeting(id) {
  if (!confirm('Delete this meeting recording?')) return;
  try {
    await api('/api/meetings/' + id, 'DELETE');
    showMsg('mtg-msg', 'Meeting deleted', false);
    await loadMeetings();
  } catch(e) { showMsg('mtg-msg', e.message, true); }
}

// ── LLM Provider Presets ──
const PROVIDERS = {
  openai:     { url: 'https://api.openai.com/v1',          chat: 'gpt-4o-mini',              asr: 'whisper-1' },
  deepseek:   { url: 'https://api.deepseek.com/v1',        chat: 'deepseek-chat',            asr: 'whisper-1' },
  groq:       { url: 'https://api.groq.com/openai/v1',     chat: 'llama-3.3-70b-versatile',  asr: 'whisper-large-v3' },
  openrouter: { url: 'https://openrouter.ai/api/v1',       chat: 'anthropic/claude-sonnet-4', asr: 'whisper-1' },
  ollama:     { url: 'http://localhost:11434/v1',           chat: 'qwen2.5:7b',               asr: 'whisper-1' },
};

function setProvider(name) {
  const p = PROVIDERS[name];
  if (!p) return;
  document.getElementById('s-baseurl').value = p.url;
  document.getElementById('s-chat').value = p.chat;
  document.getElementById('s-asr').value = p.asr;
  showMsg('set-msg', name.charAt(0).toUpperCase() + name.slice(1) + ' preset applied (remember to set API key & save)', false);
}

function onTtsProviderChange() {
  const val = document.getElementById('s-tts-provider').value;
  document.getElementById('tts-openai-fields').classList.toggle('hidden', val === 'edge');
  document.getElementById('tts-edge-fields').classList.toggle('hidden', val !== 'edge');
}

// ── Settings ──
async function loadSettings() {
  try {
    const s = await api('/api/settings');
    document.getElementById('s-apikey').value = '';
    document.getElementById('s-apikey-status').textContent = s.openai_api_key_set ? 'API key is set' : 'No API key configured (using server default)';
    document.getElementById('s-baseurl').value = s.openai_base_url;
    document.getElementById('s-chat').value = s.openai_chat_model;
    document.getElementById('s-asr').value = s.openai_asr_model;
    // TTS provider
    document.getElementById('s-tts-provider').value = s.tts_provider || '';
    onTtsProviderChange();
    if (s.tts_provider === 'edge') {
      document.getElementById('s-edge-voice').value = s.openai_tts_voice || 'xiaoxiao';
    } else {
      document.getElementById('s-tts').value = s.openai_tts_model;
      document.getElementById('s-voice').value = s.openai_tts_voice;
    }
    // Tool API keys
    document.getElementById('s-weather-key').value = '';
    document.getElementById('s-weather-key-status').textContent = s.weather_api_key_set ? 'Weather API key is set' : 'No weather API key configured';
    document.getElementById('s-weather-city').value = s.weather_city || '';
    document.getElementById('s-tavily-key').value = '';
    document.getElementById('s-tavily-key-status').textContent = s.tavily_api_key_set ? 'Tavily API key is set' : 'No Tavily API key configured';
    document.getElementById('s-youtube-key').value = '';
    document.getElementById('s-youtube-key-status').textContent = s.youtube_api_key_set ? 'YouTube API key is set' : 'No YouTube API key (using yt-dlp search)';
    // Notion
    document.getElementById('s-notion-token').value = '';
    document.getElementById('s-notion-token-status').textContent = s.notion_token_set ? 'Notion token is set' : 'No Notion token configured';
    document.getElementById('s-notion-dbid').value = s.notion_database_id || '';
  } catch(e) {}
}

async function saveSettings() {
  const body = {};
  const apikey = document.getElementById('s-apikey').value;
  if (apikey) body.openai_api_key = apikey;
  body.openai_base_url = document.getElementById('s-baseurl').value.trim();
  body.openai_chat_model = document.getElementById('s-chat').value.trim();
  body.openai_asr_model = document.getElementById('s-asr').value.trim();
  // TTS
  const ttsProvider = document.getElementById('s-tts-provider').value;
  body.tts_provider = ttsProvider;
  if (ttsProvider === 'edge') {
    body.openai_tts_model = '';
    body.openai_tts_voice = document.getElementById('s-edge-voice').value;
  } else {
    body.openai_tts_model = document.getElementById('s-tts').value.trim();
    body.openai_tts_voice = document.getElementById('s-voice').value.trim();
  }
  // Tool API keys
  const weatherKey = document.getElementById('s-weather-key').value;
  if (weatherKey) body.weather_api_key = weatherKey;
  body.weather_city = document.getElementById('s-weather-city').value.trim();
  const tavilyKey = document.getElementById('s-tavily-key').value;
  if (tavilyKey) body.tavily_api_key = tavilyKey;
  const youtubeKey = document.getElementById('s-youtube-key').value;
  if (youtubeKey) body.youtube_api_key = youtubeKey;
  // Notion
  const notionToken = document.getElementById('s-notion-token').value;
  if (notionToken) body.notion_token = notionToken;
  body.notion_database_id = document.getElementById('s-notion-dbid').value.trim();
  try {
    await api('/api/settings', 'PUT', body);
    showMsg('set-msg', 'Settings saved', false);
    await loadSettings();
  } catch(e) { showMsg('set-msg', e.message, true); }
}

// ── Notion test ──
async function testNotion() {
  const token = document.getElementById('s-notion-token').value.trim();
  const dbid = document.getElementById('s-notion-dbid').value.trim();
  const el = document.getElementById('notion-test-result');
  if (!token) { el.textContent = 'Enter Notion token first'; el.style.color = '#dc2626'; return; }
  if (!dbid) {
    el.textContent = '✅ Token OK. Database will be auto-created on first use.';
    el.style.color = '#16a34a';
    return;
  }
  el.textContent = 'Testing...'; el.style.color = '#666';
  try {
    const res = await api('/api/settings/notion-test', 'POST', {token, database_id: dbid});
    el.textContent = 'Connected! Database: ' + (res.database_title || dbid.slice(0,8) + '...');
    el.style.color = '#16a34a';
  } catch(e) {
    el.textContent = 'Failed: ' + e.message;
    el.style.color = '#dc2626';
  }
}

// ── OTA ──
async function loadOTAInfo() {
  try {
    const data = await api('/api/ota/check');
    const el = document.getElementById('ota-current');
    if (data.update_available) {
      el.textContent = 'Latest uploaded: v' + data.version + ' (' + Math.round(data.size/1024) + ' KB)';
    } else {
      el.textContent = 'No firmware uploaded yet';
    }
  } catch(e) {}
}

async function uploadFirmware() {
  const version = document.getElementById('ota-version').value.trim();
  const fileInput = document.getElementById('ota-file');
  if (!version || !fileInput.files.length) {
    return showMsg('ota-msg', 'Version and file required', true);
  }
  const fd = new FormData();
  fd.append('version', version);
  fd.append('file', fileInput.files[0]);
  try {
    const res = await fetch('/api/ota/upload-form', {
      method: 'POST', headers: {'Authorization': 'Bearer ' + TOKEN}, body: fd
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload failed');
    showMsg('ota-msg', 'Uploaded v' + version + ' (' + Math.round(data.size/1024) + ' KB)', false);
    loadOTAInfo();
  } catch(e) { showMsg('ota-msg', e.message, true); }
}

async function pushOTA() {
  if (!confirm('Push firmware update to all connected devices?')) return;
  try {
    const data = await api('/api/ota/push', 'POST');
    showMsg('ota-msg', 'Pushed to ' + data.pushed + '/' + data.total_devices + ' devices', false);
  } catch(e) { showMsg('ota-msg', e.message, true); }
}

// ── Tabs ──
function showTab(name) {
  document.querySelectorAll('[id^="tab-"]').forEach(el => el.classList.add('hidden'));
  document.getElementById('tab-' + name).classList.remove('hidden');
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  event.target.classList.add('active');
  if (name === 'meetings') loadMeetings();
  if (name === 'reminders') loadReminders();
  if (name === 'history') loadHistoryDevices();
  if (name === 'settings') loadOTAInfo();
}

// ── Stats ──
async function loadStats() {
  try {
    const s = await api('/api/stats');
    document.getElementById('stat-meetings').textContent = s.meetings;
    document.getElementById('stat-reminders').textContent = s.reminders;
    document.getElementById('stat-messages').textContent = s.total_messages;
    const details = [];
    if (s.meeting_duration_s > 0) {
      const mins = Math.floor(s.meeting_duration_s / 60);
      details.push('Total recording: ' + mins + ' min');
    }
    if (s.reminders_delivered > 0) {
      details.push(s.reminders_delivered + ' reminders delivered');
    }
    document.getElementById('stat-details').textContent = details.join(' · ');
    document.getElementById('stats-card').style.display = '';
  } catch(e) {}
}

// ── Conversation History ──
let _histDevices = [];

async function loadHistoryDevices() {
  try {
    const devices = await api('/api/devices');
    _histDevices = devices;
    const sel = document.getElementById('hist-device');
    const curVal = sel.value;
    sel.innerHTML = '<option value="">-- Select device --</option>';
    devices.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.device_id;
      opt.textContent = (d.name || d.device_id) + ' (' + d.device_id + ')';
      sel.appendChild(opt);
    });
    if (curVal) { sel.value = curVal; loadConversation(); }
    else if (devices.length === 1) { sel.value = devices[0].device_id; loadConversation(); }
  } catch(e) {}
}

async function loadConversation() {
  const deviceId = document.getElementById('hist-device').value;
  const chatEl = document.getElementById('hist-chat');
  const emptyEl = document.getElementById('hist-empty');
  const clearBtn = document.getElementById('hist-clear-btn');
  chatEl.style.display = 'none'; chatEl.innerHTML = '';
  emptyEl.classList.add('hidden');
  clearBtn.style.display = 'none';
  if (!deviceId) return;
  try {
    const data = await api('/api/devices/' + encodeURIComponent(deviceId) + '/conversation');
    const msgs = data.messages || [];
    if (msgs.length === 0) { emptyEl.classList.remove('hidden'); return; }
    chatEl.style.display = '';
    clearBtn.style.display = '';
    msgs.forEach(m => {
      const div = document.createElement('div');
      div.className = 'chat-msg ' + (m.role || 'user');
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = m.content || '';
      div.appendChild(bubble);
      chatEl.appendChild(div);
    });
    chatEl.scrollTop = chatEl.scrollHeight;
  } catch(e) { showMsg('hist-msg', e.message, true); }
}

async function clearConversation() {
  const deviceId = document.getElementById('hist-device').value;
  if (!deviceId || !confirm('Clear conversation history for this device?')) return;
  try {
    await api('/api/devices/' + encodeURIComponent(deviceId) + '/conversation', 'DELETE');
    showMsg('hist-msg', 'History cleared', false);
    loadConversation();
  } catch(e) { showMsg('hist-msg', e.message, true); }
}

// ── Utils ──
function showMsg(id, text, isErr) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = 'msg ' + (isErr ? 'msg-err' : 'msg-ok');
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 4000);
}

// ── Init ──
if (TOKEN) { enterApp(); }
</script>
</body>
</html>"""
