from __future__ import annotations

import wave
from pathlib import Path

import pytest

from sound_catcher.audio.tts import CachedSynthesizer
from sound_catcher.dispatcher import Dispatcher
from sound_catcher.history import History
from sound_catcher.profile import parse_profile

PROFILE = {
    "rules": {
        "backup": {
            "systems": ["backup"],
            "severity": ["error"],
            "sound": "alarm.wav",
            "say": "Backup auf {{ system }} fehlgeschlagen: {{ title }}",
            "volume": 90,
        },
        "errors": {"severity": ["error", "critical"], "sound": "chime.wav", "say": "Fehler: {{ title }}"},
        "success": {"severity": ["success"], "sound": "success.wav"},
        "quiet": {"severity": ["info"], "silent": True},
    }
}


def fake_render(text: str, wav_file: wave.Wave_write) -> None:
    """Stands in for Piper: a few frames, so the file is a real WAV."""
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(16000)
    wav_file.writeframes(b"\0\0" * 16)


class FakePlayer:
    def __init__(self, accept: bool = True) -> None:
        self.jobs = []
        self.accept = accept
        self.alive = True

    def play(self, job) -> bool:
        if self.accept:
            self.jobs.append(job)
        return self.accept

    def close(self) -> None:
        pass


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def sounds_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sounds"
    d.mkdir()
    for name in ("alarm.wav", "chime.wav", "success.wav"):
        (d / name).write_bytes(b"RIFF")
    return d


@pytest.fixture
def synth(tmp_path: Path) -> CachedSynthesizer:
    return CachedSynthesizer("test-voice", tmp_path / "cache", fake_render)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_dispatcher(sounds_dir, synth, clock):
    def _make(profile: dict | None = None, player=None, browser: bool = True, dedup: float = 60.0, history=None):
        history = history or History(20)
        d = Dispatcher(
            parse_profile(profile or PROFILE),
            history,
            sounds_dir,
            synthesizer=synth,
            player=player,
            browser=browser,
            dedup_window=dedup,
            clock=clock,
        )
        return d, history

    return _make
