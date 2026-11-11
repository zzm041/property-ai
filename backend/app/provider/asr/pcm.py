"""Normalise an uploaded recording into the PCM WAV the ASR adapter accepts.

The frozen local adapter (``scripts/ops/local_ai_http.py``) hands the uploaded
bytes straight to the ASR runner, so it only accepts a RIFF WAV with one
channel, 16-bit samples and 16 kHz, at most 480000 frames and 1 MB. Phones
record mp3, m4a/aac or webm, and the adapter answers those with
``PCM_REQUIRED`` — the mini-program saw exactly that.

The conversion lives on the app side instead of inside the adapter: the adapter
is a hash-pinned frozen artifact shared with the work-order runtime, and the app
already owns the mime type, the attachment limits and the ACL. PyAV carries
FFmpeg inside its wheel, so the image needs no system package.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.provider.errors import (
    ProviderAudioTranscodeError,
    ProviderAudioTranscodeUnavailableError,
)

PCM_SAMPLE_RATE = 16_000
PCM_SAMPLE_WIDTH = 2
PCM_CHANNELS = 1
PCM_MAX_FRAMES = 480_000
PCM_MAX_BYTES = 1_000_000
# Refuse absurd uploads before decoding them; the ASR window is 30 s any way.
TRANSCODE_MAX_BYTES = 25 * 1024 * 1024

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PcmAudio:
    """A recording that satisfies the adapter's PCM contract."""

    content: bytes
    duration_seconds: float
    source_bytes: int
    source_mime_type: str
    transcoded: bool


def _pcm_frames(content: bytes) -> int | None:
    """Frame count when ``content`` already is the adapter's PCM WAV."""

    try:
        with wave.open(io.BytesIO(content)) as handle:
            if (
                handle.getnchannels() != PCM_CHANNELS
                or handle.getsampwidth() != PCM_SAMPLE_WIDTH
                or handle.getframerate() != PCM_SAMPLE_RATE
                or handle.getcomptype() != "NONE"
            ):
                return None
            frames = handle.getnframes()
    except (wave.Error, EOFError):
        return None
    if frames <= 0 or frames > PCM_MAX_FRAMES or len(content) > PCM_MAX_BYTES:
        return None
    return frames


def is_pcm_wav(content: bytes) -> bool:
    """True when the adapter would accept these bytes unchanged."""

    return _pcm_frames(content) is not None


def _load_av() -> Any:
    try:
        import av
    except Exception as exc:  # pragma: no cover - wheel missing at runtime
        raise ProviderAudioTranscodeUnavailableError(
            "audio decoding is unavailable: the PyAV decoder is not installed"
        ) from exc
    return av


def _decode_to_pcm(av: Any, content: bytes, mime_type: str) -> bytes:
    """Decode any container the phone produced into s16le mono 16 kHz."""

    try:
        container = av.open(io.BytesIO(content))
    except Exception as exc:
        raise ProviderAudioTranscodeError(
            f"the recording could not be opened for decoding ({mime_type})"
        ) from exc

    buffer = bytearray()
    limit = PCM_MAX_FRAMES * PCM_SAMPLE_WIDTH
    try:
        stream = next(
            (item for item in container.streams if item.type == "audio"), None
        )
        if stream is None:
            raise ProviderAudioTranscodeError(
                f"the recording has no audio track ({mime_type})"
            )
        resampler = av.AudioResampler(
            format="s16", layout="mono", rate=PCM_SAMPLE_RATE
        )
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                buffer += resampled.to_ndarray().tobytes()
            if len(buffer) >= limit:
                break
        if len(buffer) < limit:
            for resampled in resampler.resample(None):
                buffer += resampled.to_ndarray().tobytes()
    except ProviderAudioTranscodeError:
        raise
    except Exception as exc:
        raise ProviderAudioTranscodeError(
            f"the recording could not be decoded ({mime_type})"
        ) from exc
    finally:
        container.close()

    if not buffer:
        raise ProviderAudioTranscodeError(
            f"the recording decoded to no samples ({mime_type})"
        )
    return bytes(buffer[:limit])


def _wav_container(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(PCM_CHANNELS)
        handle.setsampwidth(PCM_SAMPLE_WIDTH)
        handle.setframerate(PCM_SAMPLE_RATE)
        handle.setnframes(len(pcm) // PCM_SAMPLE_WIDTH)
        handle.writeframes(pcm)
    return out.getvalue()


def to_pcm_wav(content: bytes, mime_type: str) -> PcmAudio:
    """Return PCM the adapter accepts, converting when the upload is not.

    Anything that cannot be decoded raises ``ProviderAudioTranscodeError`` so the
    failure reaches ``ai_call_logs`` and the analysis record; it never degrades
    into "this work order had no speech".
    """

    frames = _pcm_frames(content)
    if frames is not None:
        return PcmAudio(
            content=content,
            duration_seconds=frames / PCM_SAMPLE_RATE,
            source_bytes=len(content),
            source_mime_type=mime_type,
            transcoded=False,
        )

    if not content:
        raise ProviderAudioTranscodeError(
            f"the recording is empty ({mime_type}): nothing to transcribe"
        )
    if len(content) > TRANSCODE_MAX_BYTES:
        raise ProviderAudioTranscodeError(
            f"the recording is {len(content)} bytes, above the {TRANSCODE_MAX_BYTES} "
            f"byte decode limit ({mime_type})"
        )

    pcm = _decode_to_pcm(_load_av(), content, mime_type)
    wav = _wav_container(pcm)
    if len(wav) > PCM_MAX_BYTES:  # pragma: no cover - bounded by PCM_MAX_FRAMES
        raise ProviderAudioTranscodeError(
            f"the converted recording is {len(wav)} bytes, above the adapter limit"
        )
    duration = len(pcm) // PCM_SAMPLE_WIDTH / PCM_SAMPLE_RATE
    return PcmAudio(
        content=wav,
        duration_seconds=duration,
        source_bytes=len(content),
        source_mime_type=mime_type,
        transcoded=True,
    )
