# HiTony Server

WebSocket + HTTP admin server for HiTony devices. Uses OpenAI for ASR (Whisper), LLM (GPT), and TTS.

## Architecture

```
Device (ESP32-S3) ──WebSocket:9001──> ws_server.py (websockets)
                                        ├── ASR: OpenAI Whisper
                                        ├── LLM: OpenAI GPT
                                        └── TTS: OpenAI TTS → Opus
Browser ──HTTP:8000──> main.py (FastAPI)
                        ├── /health
                        ├── /admin (web UI)
                        └── /ota/
```

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure OpenAI
export OPENAI_API_KEY="sk-..."
export OPENAI_CHAT_MODEL="gpt-4o-mini"

# Run both servers
python run_server.py
```

## Device registration

```bash
curl -X POST http://localhost:8000/register \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"hitony-001","token":"devtoken"}'
```

## WebSocket protocol (xiaozhi-compatible)

### Device -> Server (text JSON)
- `hello`: `{"type":"hello","device_id":"...","fw":"...","listen_mode":"auto"}`
- `listen`: `{"type":"listen","state":"detect|start|stop","mode":"auto","text":"Hi ESP"}`
- `abort`: `{"type":"abort","reason":"wake_word_detected"}`

### Device -> Server (binary)
- Opus packets (60ms frames, 16kHz mono) after `listen(start)`

### Server -> Device (text JSON)
- `hello`: session info + audio params
- `asr_text`: `{"type":"asr_text","text":"..."}`
- `tts_start` / `tts_end`

### Server -> Device (binary)
- Opus packets during TTS playback

## systemd deployment

```bash
sudo cp hitony-server-ws.service /etc/systemd/system/hitony-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now hitony-server
```
