"""Tests for app/tts.py — TTS synthesis, resampling, LRU cache."""
import struct
from unittest.mock import AsyncMock, patch, MagicMock

import numpy as np
import pytest

from app.tts import (
    _resample_24k_to_16k, _resample_and_encode, _tts_cache,
    _TTS_CACHE_MAX, _TTS_CACHE_MAX_CHARS, _get_client, synthesize_tts,
)


class TestResample24kTo16k:
    def test_output_length(self):
        samples_24k = np.zeros(480, dtype=np.int16)
        pcm_16k = _resample_24k_to_16k(samples_24k.tobytes())
        assert len(pcm_16k) // 2 == 480 * 2 // 3

    def test_preserves_silence(self):
        pcm_16k = _resample_24k_to_16k(b"\x00" * (480 * 2))
        assert np.all(np.frombuffer(pcm_16k, dtype=np.int16) == 0)

    def test_preserves_signal(self):
        t = np.arange(480) / 24000
        sine = (np.sin(2 * np.pi * 1000 * t) * 10000).astype(np.int16)
        out = np.frombuffer(_resample_24k_to_16k(sine.tobytes()), dtype=np.int16)
        assert np.max(np.abs(out)) > 1000

    def test_empty_input(self):
        with pytest.raises(ValueError):
            _resample_24k_to_16k(b"")

    def test_output_dtype_int16(self):
        pcm_16k = _resample_24k_to_16k(b"\x00" * (960 * 2))
        assert np.frombuffer(pcm_16k, dtype=np.int16).dtype == np.int16


class TestResampleAndEncode:
    def test_returns_opus_packets(self):
        pcm_24k = b"\x00" * (1440 * 2)
        packets = _resample_and_encode(pcm_24k)
        assert isinstance(packets, list)
        assert len(packets) >= 1

    def test_multiple_frames(self):
        pcm_24k = b"\x00" * (48000 * 2)
        packets = _resample_and_encode(pcm_24k)
        assert len(packets) > 10


class TestGetClient:
    def test_default_client(self):
        assert _get_client(None) is not None

    def test_session_without_key(self):
        session = MagicMock()
        session.config.openai_api_key = ""
        from app.tts import _client
        assert _get_client(session) is _client


class TestSynthesizeTts:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        _tts_cache.clear()
        yield
        _tts_cache.clear()

    @pytest.mark.asyncio
    async def test_openai_tts_success(self):
        mock_response = MagicMock()
        mock_response.content = b"\x00" * (2880 * 2)
        mock_client = AsyncMock()
        mock_client.audio.speech.create = AsyncMock(return_value=mock_response)

        with patch("app.tts._get_client", return_value=mock_client):
            result = await synthesize_tts("这是一个测试文本超过二十字")
        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        cached_packets = [b"\x00" * 20, b"\x00" * 20]
        cache_key = ("好的", "tts-1", "alloy")
        _tts_cache[cache_key] = cached_packets
        result = await synthesize_tts("好的")
        assert result is cached_packets

    @pytest.mark.asyncio
    async def test_short_text_cached(self):
        mock_response = MagicMock()
        mock_response.content = b"\x00" * (2880 * 2)
        mock_client = AsyncMock()
        mock_client.audio.speech.create = AsyncMock(return_value=mock_response)

        with patch("app.tts._get_client", return_value=mock_client):
            await synthesize_tts("测试")
        assert len(_tts_cache) == 1

    @pytest.mark.asyncio
    async def test_pro_mode_fallback(self):
        session = MagicMock()
        session.config.openai_api_key = "sk-custom"
        session.config.openai_base_url = "https://custom.api"
        session.config.get.side_effect = lambda f, d: d

        mock_response = MagicMock()
        mock_response.content = b"\x00" * (2880 * 2)

        mock_custom = AsyncMock()
        mock_custom.audio.speech.create = AsyncMock(side_effect=Exception("fail"))

        mock_default = AsyncMock()
        mock_default.audio.speech.create = AsyncMock(return_value=mock_response)

        with patch("app.tts._get_client", return_value=mock_custom), \
             patch("app.tts._client", mock_default):
            result = await synthesize_tts("测试回退测试回退测试回退", session=session)
        assert isinstance(result, list)


class TestTtsCache:
    def test_cache_is_dict(self):
        from collections import OrderedDict
        assert isinstance(_tts_cache, OrderedDict)

    def test_cache_constants(self):
        assert _TTS_CACHE_MAX == 50
        assert _TTS_CACHE_MAX_CHARS == 20
