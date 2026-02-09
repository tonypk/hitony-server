import os
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile
from faster_whisper import WhisperModel

MODEL_NAME = os.getenv("WHISPER_MODEL", "small")

app = FastAPI()
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), rate: int = Form(16000), channels: int = Form(1)):
    data = await file.read()
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

    segments, _ = model.transcribe(audio, language="en", beam_size=1)
    text = "".join([seg.text for seg in segments])
    return {"text": text.strip()}
