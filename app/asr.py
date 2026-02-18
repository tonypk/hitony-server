import io
import struct
import logging
from collections import OrderedDict
from typing import Optional
from openai import AsyncOpenAI
from .config import settings
from .session import Session

logger = logging.getLogger(__name__)

# Global default client
_client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
)

# Per-user client cache with LRU eviction: (base_url, api_key) → AsyncOpenAI
_CLIENT_CACHE_MAX = 20
_client_cache: OrderedDict[tuple[str, str], AsyncOpenAI] = OrderedDict()


def _get_client(session: Optional[Session] = None) -> AsyncOpenAI:
    """Return cached per-user client if session has custom API key, else global."""
    if session and session.config.openai_api_key:
        base_url = session.config.get("openai_base_url", settings.openai_base_url)
        key = (base_url, session.config.openai_api_key)
        if key not in _client_cache:
            if len(_client_cache) >= _CLIENT_CACHE_MAX:
                _client_cache.popitem(last=False)
            _client_cache[key] = AsyncOpenAI(api_key=session.config.openai_api_key, base_url=base_url)
        _client_cache.move_to_end(key)
        return _client_cache[key]
    return _client

def pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Convert raw PCM16 mono 16kHz to WAV format in memory"""
    num_channels = settings.pcm_channels
    sample_rate = settings.pcm_sample_rate
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_bytes)

    buf = io.BytesIO()
    # RIFF header
    buf.write(b'RIFF')
    buf.write(struct.pack('<I', 36 + data_size))
    buf.write(b'WAVE')
    # fmt chunk
    buf.write(b'fmt ')
    buf.write(struct.pack('<I', 16))  # chunk size
    buf.write(struct.pack('<H', 1))   # PCM format
    buf.write(struct.pack('<H', num_channels))
    buf.write(struct.pack('<I', sample_rate))
    buf.write(struct.pack('<I', byte_rate))
    buf.write(struct.pack('<H', block_align))
    buf.write(struct.pack('<H', bits_per_sample))
    # data chunk
    buf.write(b'data')
    buf.write(struct.pack('<I', data_size))
    buf.write(pcm_bytes)
    return buf.getvalue()


# Whisper hallucination patterns — produced from silence/noise input
_HALLUCINATIONS = {
    # English
    "thank you", "thank you for watching", "thanks for watching",
    "thanks", "bye", "goodbye", "all right", "you", "the end",
    "subscribe", "like and subscribe", "see you next time",
    "so", "okay", "yeah", "yes", "no", "hmm", "uh",
    # Chinese
    "谢谢观看", "感谢观看", "请订阅", "点赞", "订阅",
    "谢谢大家", "谢谢", "再见", "好的", "嗯",
    "字幕", "字幕由", "字幕提供",
}

# Longer hallucination patterns — match as substrings
_ASR_PROMPT = (
    "HiTony语音助手。播放音乐，放首歌，来一首，下一首，切歌，"
    "播放周杰伦的歌，播放Taylor Swift，播放邓紫棋，暂停，停止播放，继续播放，"
    "音量大一点，音量小一点，声音调到50，提醒我，每天提醒我，每周提醒我，每月提醒我，"
    "工作日提醒我，每天8点提醒我，查看提醒，取消提醒，设置闹钟，设个闹钟7点，"
    "查看闹钟，取消闹钟，今天有什么安排，今天的安排，每日简报，今天天气怎么样，"
    "倒计时，取消倒计时，搜索，帮我查一下，开始会议，结束会议，"
    "记一下，笔记，帮我记，备忘，清空对话，新对话，忘掉对话，你好。"
)

_HALLUCINATION_SUBSTRINGS = [
    "点赞", "订阅", "转发", "打赏", "关注",
    "字幕由", "字幕提供", "subtitles by",
    "thank you for watching", "thanks for watching",
    "like and subscribe",
    "明镜", "栏目", "支持明镜",
    "请不吝", "视频来源",
]


async def transcribe_pcm(pcm_bytes: bytes, session: Optional[Session] = None) -> str:
    """Transcribe PCM audio using OpenAI Whisper API"""
    wav_bytes = pcm_to_wav(pcm_bytes)
    duration_s = len(pcm_bytes) / 2 / settings.pcm_sample_rate
    logger.info(f"ASR: sending {len(pcm_bytes)} bytes PCM ({duration_s:.1f}s, {len(wav_bytes)} bytes WAV) to Whisper")

    # Filter very short recordings (<0.5s) — usually noise/accidental triggers
    if duration_s < 0.5:
        logger.info(f"ASR: skipping short audio ({duration_s:.1f}s < 0.5s)")
        return ""

    wav_file = io.BytesIO(wav_bytes)
    wav_file.name = "audio.wav"

    client = _get_client(session)
    asr_model = (session.config.get("openai_asr_model", settings.openai_asr_model)
                 if session else settings.openai_asr_model)

    # Try user's OpenClaw first (Pro mode), fallback to default if unsupported
    try:
        transcript = await client.audio.transcriptions.create(
            model=asr_model,
            file=wav_file,
            temperature=0,
            language="zh",
            # Whisper prompt: vocabulary hints for common commands
            prompt=_ASR_PROMPT,
        )
    except Exception as e:
        # Fallback to default API if user's OpenClaw doesn't support ASR
        if session and session.config.openai_base_url:
            logger.warning(f"ASR: Pro mode failed ({e}), falling back to default API")
            wav_file.seek(0)  # Reset file pointer
            transcript = await _client.audio.transcriptions.create(
                model=settings.openai_asr_model,
                file=wav_file,
                temperature=0,
                language="zh",
                prompt=_ASR_PROMPT,
            )
        else:
            raise  # Re-raise if not in Pro mode

    text = transcript.text.strip()
    logger.info(f"ASR result: {text}")

    # Filter known Whisper hallucinations (noise/silence → fake output)
    normalized = text.lower().rstrip(".!?,。！？，")
    if normalized in _HALLUCINATIONS:
        logger.warning(f"ASR: filtered hallucination (exact): '{text}'")
        return ""

    # Substring hallucination check (YouTube-style garbage)
    lower_text = text.lower()
    for pattern in _HALLUCINATION_SUBSTRINGS:
        if pattern in lower_text:
            logger.warning(f"ASR: filtered hallucination (substring '{pattern}'): '{text}'")
            return ""

    return text
