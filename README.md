# EchoEar Server (MVP)

WebSocket gateway + ASR/TTS + OpenClaw integration for EchoEar devices.

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export OPENCLAW_URL="http://YOUR_OPENCLAW_HOST:18789/v1/responses"
export OPENCLAW_TOKEN="..."
export OPENCLAW_MODEL="openclaw:main"

uvicorn app.main:app --host 0.0.0.0 --port 9001
```

## Device registration
```bash
curl -X POST http://localhost:9001/register \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"echoear-001","token":"devtoken"}'
```

## OTA config
`GET /ota/` returns websocket URL and protocol version.

## WebSocket protocol (MVP)
Text JSON:
- `hello`: `{ "type":"hello", "device_id":"...", "fw":"..." }`
- `wake`: `{ "type":"wake", "device_id":"..." }`
- `audio_start`: `{ "type":"audio_start", "device_id":"...", "format":"pcm16", "rate":16000, "channels":1 }`
- `audio_end`: `{ "type":"audio_end", "device_id":"..." }`

Binary:
- raw PCM16 mono frames after `audio_start`.

Server -> device:
- `asr_text`: `{ "type":"asr_text", "text":"..." }`
- `tts_start` / `tts_end`
- Binary PCM16 frames in between.

## ASR service (faster-whisper)
```bash
cd services
python3 -m venv .venv
source .venv/bin/activate
pip install -r ../requirements-asr.txt
export WHISPER_MODEL=small
uvicorn asr_service:app --host 0.0.0.0 --port 9101
```

## TTS service (Piper)
```bash
cd services
python3 -m venv .venv
source .venv/bin/activate
pip install -r ../requirements-tts.txt
export PIPER_BIN=piper
export PIPER_MODEL=/path/to/model.onnx
uvicorn tts_service:app --host 0.0.0.0 --port 9102
```
