"""One offline CPU call. Only the parent runtime invokes this script."""
import base64
import json
import io
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def bounded_audio(content):
    """Decode at most 30s + one frame, before importing/loading an ASR model."""
    import soundfile as sf
    with sf.SoundFile(io.BytesIO(content)) as audio:
        if audio.samplerate != 16000 or audio.channels != 1:
            raise ValueError("AUDIO_FORMAT")
        samples = audio.read(frames=480001, dtype="float32", always_2d=True)
    if not 0 < samples.shape[0] <= 480000:
        raise ValueError("AUDIO_DURATION")
    import numpy as np
    if not np.isfinite(samples).all():
        raise ValueError("AUDIO_NONFINITE")
    # The frozen adapter subsequently decodes only this bounded PCM buffer.
    normalized = io.BytesIO()
    sf.write(normalized, samples, 16000, format="WAV", subtype="FLOAT")
    return normalized.getvalue()


def main():
    payload = json.loads(sys.stdin.buffer.read(24 * 1024 * 1024))
    if os.name == "nt":
        from scripts.ops.local_ai_process import join_request_job
        join_request_job(payload["job"])
    elif os.getsid(0) != os.getpid():
        raise RuntimeError("OWNED_SESSION_REQUIRED")
    from scripts.ops.local_ai_models import offline, load, infer
    kind = payload["kind"]
    if kind not in ("asr", "embedding", "reranker"):
        raise ValueError("MODEL_KIND")
    offline(payload["cache"])
    value = base64.b64decode(payload["value"], validate=True) if kind == "asr" else payload["value"]
    if kind == "asr":
        value = bounded_audio(value)
    result = infer(kind, load(kind, payload["root"]), value)
    Path(payload["result"]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
