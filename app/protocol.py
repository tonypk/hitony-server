from pydantic import BaseModel
from typing import Literal, Optional

class Hello(BaseModel):
    type: Literal["hello"]
    device_id: str
    fw: Optional[str] = None

class Wake(BaseModel):
    type: Literal["wake"]
    device_id: str

class AudioStart(BaseModel):
    type: Literal["audio_start"]
    device_id: str
    format: Literal["pcm16"]
    rate: int
    channels: int

class AudioEnd(BaseModel):
    type: Literal["audio_end"]
    device_id: str

class AsrText(BaseModel):
    type: Literal["asr_text"]
    text: str

class TtsStart(BaseModel):
    type: Literal["tts_start"]

class TtsEnd(BaseModel):
    type: Literal["tts_end"]

class ErrorMsg(BaseModel):
    type: Literal["error"]
    message: str
