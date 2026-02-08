import os
import subprocess
from fastapi import FastAPI
from fastapi.responses import Response

app = FastAPI()

PIPER_BIN = os.getenv("PIPER_BIN", "piper")
PIPER_MODEL = os.getenv("PIPER_MODEL", "")
PIPER_SPEAKER = os.getenv("PIPER_SPEAKER", "")

@app.post("/synthesize")
async def synthesize(payload: dict):
    text = payload.get("text", "")
    if not text:
        return Response(status_code=400, content=b"text required")
    if not PIPER_MODEL:
        return Response(status_code=500, content=b"PIPER_MODEL not set")

    cmd = [PIPER_BIN, "--model", PIPER_MODEL, "--output_raw"]
    if PIPER_SPEAKER:
        cmd += ["--speaker", PIPER_SPEAKER]

    proc = subprocess.run(cmd, input=text.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return Response(status_code=500, content=proc.stderr)

    # Return raw PCM16
    return Response(content=proc.stdout, media_type="application/octet-stream")
