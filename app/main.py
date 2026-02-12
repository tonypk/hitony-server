"""
HiTony Server - FastAPI HTTP endpoints + admin dashboard.
WebSocket server runs separately via ws_server.py (launched by run_server.py).
"""
import json
import os
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import settings
from .registry import registry
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
    registry.register(device_id, token)
    logger.info(f"Registered device: {device_id}")
    return {"ok": True}

@app.get("/ota/")
async def ota(request: Request):
    host = request.headers.get("host", f"{settings.ws_host}:{settings.ws_port}")
    return {"websocket": {"url": f"ws://{host}/ws", "version": 3}}


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
        <thead><tr><th>Device ID</th><th>Name</th><th>Last Seen</th><th></th></tr></thead>
        <tbody id="dev-tbody"></tbody>
      </table>
      <p id="dev-empty" class="text-sm mt text-center hidden">No devices yet</p>
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
    <button class="btn btn-primary mt" style="width:100%" onclick="saveSettings()">Save Settings</button>
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
}

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
    const devices = await api('/api/devices');
    const tbody = document.getElementById('dev-tbody');
    const empty = document.getElementById('dev-empty');
    tbody.innerHTML = '';
    if (devices.length === 0) { empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    devices.forEach(d => {
      const seen = d.last_seen ? new Date(d.last_seen).toLocaleString() : 'Never';
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${d.device_id}</td><td>${d.name||'-'}</td><td>${seen}</td>
        <td><button class="btn btn-danger btn-sm" onclick="delDevice('${d.device_id}')">Remove</button></td>`;
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
      tr.innerHTML = `<td>${dt}</td><td>${r.message}</td><td style="${statusCls}">${status}</td>
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
  try {
    await api('/api/settings', 'PUT', body);
    showMsg('set-msg', 'Settings saved', false);
    await loadSettings();
  } catch(e) { showMsg('set-msg', e.message, true); }
}

// ── Tabs ──
function showTab(name) {
  document.querySelectorAll('[id^="tab-"]').forEach(el => el.classList.add('hidden'));
  document.getElementById('tab-' + name).classList.remove('hidden');
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  event.target.classList.add('active');
  if (name === 'reminders') loadReminders();
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
