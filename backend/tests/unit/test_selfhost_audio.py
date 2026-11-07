"""Bounded audio decode must happen before any ASR model load."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.selfhost import worker


@pytest.mark.parametrize("rate,channels", [(48000, 1), (16000, 2)])
def test_wrong_format_rejected_before_read(monkeypatch, rate, channels):
    reader = MagicMock()
    reader.samplerate, reader.channels = rate, channels
    reader.__enter__.return_value = reader
    sf = SimpleNamespace(SoundFile=lambda source: reader)
    monkeypatch.setitem(sys.modules, "soundfile", sf)
    with pytest.raises(ValueError, match="AUDIO_FORMAT"):
        worker.bounded_audio(b"fixture")
    reader.read.assert_not_called()


def test_long_audio_read_is_capped_even_if_metadata_understates_length(monkeypatch):
    reader = MagicMock()
    reader.samplerate, reader.channels = 16000, 1
    reader.__enter__.return_value = reader
    reader.read.return_value = SimpleNamespace(shape=(480001, 1))
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(SoundFile=lambda source: reader))
    with pytest.raises(ValueError, match="AUDIO_DURATION"):
        worker.bounded_audio(b"fixture")
    reader.read.assert_called_once_with(frames=480001, dtype="float32", always_2d=True)
