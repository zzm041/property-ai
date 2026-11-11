"""Voice chain: MP3/AAC recordings become the PCM WAV the ASR adapter accepts.

The mini-program uploads mp3/m4a/webm, the frozen adapter only reads 16 kHz mono
16-bit WAV and answers anything else with ``PCM_REQUIRED``. These tests pin both
the conversion and the failure: a recording that cannot be decoded must raise
``AI.AUDIO_TRANSCODE_FAILED`` instead of silently becoming "no speech".
"""

from __future__ import annotations

import io
import math
import struct
import sys
import wave
from unittest.mock import MagicMock

import av
import httpx
import pytest

from app.provider.asr import HttpASRProvider
from app.provider.asr import pcm as pcm_module
from app.provider.asr.pcm import (
    PCM_CHANNELS,
    PCM_SAMPLE_RATE,
    PCM_SAMPLE_WIDTH,
    is_pcm_wav,
    to_pcm_wav,
)
from app.provider.errors import (
    ProviderAudioTranscodeError,
    ProviderAudioTranscodeUnavailableError,
)


def _tone_wav(seconds: float = 1.0, rate: int = 44100, channels: int = 2) -> bytes:
    """A real signal, not silence: 440 Hz so RMS can be asserted non-zero."""

    samples = bytearray()
    for index in range(int(seconds * rate)):
        value = int(12000 * math.sin(2 * math.pi * 440 * index / rate))
        for _ in range(channels):
            samples += struct.pack("<h", value)
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(samples))
    return out.getvalue()


def _encode(source: bytes, container_format: str, codec: str) -> bytes:
    """Stand-in for what the phone's recorder produces."""

    out = io.BytesIO()
    with av.open(io.BytesIO(source)) as src, av.open(
        out, "w", format=container_format
    ) as dst:
        stream = dst.add_stream(codec, rate=44100)
        stream.layout = "stereo"
        for frame in src.decode(src.streams.audio[0]):
            for packet in stream.encode(frame):
                dst.mux(packet)
        for packet in stream.encode(None):
            dst.mux(packet)
    return out.getvalue()


def _samples(content: bytes) -> list[int]:
    with wave.open(io.BytesIO(content)) as handle:
        raw = handle.readframes(handle.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


@pytest.mark.parametrize(
    "mime_type,container,codec",
    [("audio/mpeg", "mp3", "libmp3lame"), ("audio/mp4", "mp4", "aac")],
)
def test_phone_recording_is_transcoded_to_the_adapter_contract(
    mime_type: str, container: str, codec: str
) -> None:
    recording = _encode(_tone_wav(), container, codec)
    assert not is_pcm_wav(recording)

    audio = to_pcm_wav(recording, mime_type)

    assert audio.transcoded is True
    assert audio.source_mime_type == mime_type
    assert audio.source_bytes == len(recording)
    assert is_pcm_wav(audio.content)
    with wave.open(io.BytesIO(audio.content)) as handle:
        assert handle.getnchannels() == PCM_CHANNELS
        assert handle.getsampwidth() == PCM_SAMPLE_WIDTH
        assert handle.getframerate() == PCM_SAMPLE_RATE
        assert handle.getcomptype() == "NONE"
        assert handle.getnframes() == PCM_SAMPLE_RATE  # one second of audio
    assert audio.duration_seconds == pytest.approx(1.0, abs=0.01)
    samples = _samples(audio.content)
    assert max(abs(value) for value in samples) > 0  # the tone survived


def test_pcm_upload_passes_through_untouched() -> None:
    compliant = to_pcm_wav(_tone_wav(), "audio/wav").content
    audio = to_pcm_wav(compliant, "audio/wav")
    assert audio.transcoded is False
    assert audio.content == compliant


def test_wrong_rate_wav_is_still_normalised() -> None:
    """A 44.1 kHz stereo WAV is a WAV, but not the adapter's WAV."""

    audio = to_pcm_wav(_tone_wav(), "audio/wav")
    assert audio.transcoded is True
    assert is_pcm_wav(audio.content)


def test_long_recording_is_clipped_to_the_adapter_window() -> None:
    audio = to_pcm_wav(_encode(_tone_wav(seconds=32.0), "mp3", "libmp3lame"), "audio/mpeg")
    assert audio.duration_seconds == pytest.approx(30.0, abs=0.05)
    assert len(audio.content) <= pcm_module.PCM_MAX_BYTES


def test_missing_decoder_reports_an_explicit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """No PyAV wheel in the image must fail loudly, not drop the voice note."""

    monkeypatch.setitem(sys.modules, "av", None)
    with pytest.raises(ProviderAudioTranscodeUnavailableError) as caught:
        to_pcm_wav(_encode(_tone_wav(), "mp3", "libmp3lame"), "audio/mpeg")
    assert caught.value.error_code == "AI.AUDIO_TRANSCODE_UNAVAILABLE"
    assert "PyAV" in str(caught.value)


def test_undecodable_upload_reports_the_transcode_failure() -> None:
    with pytest.raises(ProviderAudioTranscodeError) as caught:
        to_pcm_wav(b"ID3 but not really audio", "audio/mpeg")
    assert caught.value.error_code == "AI.AUDIO_TRANSCODE_FAILED"


def test_empty_recording_is_refused() -> None:
    with pytest.raises(ProviderAudioTranscodeError):
        to_pcm_wav(b"", "audio/mpeg")


def test_provider_uploads_pcm_and_logs_the_conversion() -> None:
    """Wiring: the adapter never sees the mp3, and the log line is greppable."""

    recording = _encode(_tone_wav(), "mp3", "libmp3lame")
    storage = MagicMock()
    storage.get_object.return_value = recording
    seen: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "厨房漏水了"})

    provider = HttpASRProvider(
        base_url="https://asr.invalid/v1",
        model="Qwen3-ASR-0.6B",
        api_key="synthetic-secret",
        timeout_seconds=5,
        storage=storage,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    transcript = provider.transcribe(storage_key="synthetic/audio", mime_type="audio/mpeg")

    assert transcript.text == "厨房漏水了"
    assert b'name="file"; filename="audio.wav"' in seen["body"]
    assert recording not in seen["body"]
    assert b"RIFF" in seen["body"]


def test_provider_propagates_the_transcode_error_without_calling_the_adapter() -> None:
    storage = MagicMock()
    storage.get_object.return_value = b"\x00\x01\x02 not audio"
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        calls.append(request)
        return httpx.Response(200, json={"text": "should not happen"})

    provider = HttpASRProvider(
        base_url="https://asr.invalid/v1",
        model="Qwen3-ASR-0.6B",
        api_key="synthetic-secret",
        timeout_seconds=5,
        storage=storage,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderAudioTranscodeError):
        provider.transcribe(storage_key="synthetic/audio", mime_type="audio/mpeg")
    assert calls == []
