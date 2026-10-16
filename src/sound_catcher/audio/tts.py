"""Text to speech with Piper, cached on disk.

Announcements repeat ("backup failed on nas01" comes every night it fails),
so every synthesized text is kept under the hash of voice and text, and a
repeat costs a file lookup. The cache is pruned to ``MAX_CACHE_FILES``, oldest
first.

Piper is an optional dependency (``pip install .[tts]``). Without it, or
without ``TTS_VOICE``, there is no speech: rules still play their clips, and
their ``say`` text still shows in the history.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import wave
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

MAX_CACHE_FILES = 500


class Synthesizer(Protocol):
    available: bool

    def synthesize(self, text: str) -> Path | None: ...


class NoSpeech:
    """What runs without a voice: every announcement is clip-only."""

    available = False

    def synthesize(self, text: str) -> Path | None:
        return None


class CachedSynthesizer:
    """Caches whatever `render(text, wav_file)` writes, keyed by voice and text."""

    available = True

    def __init__(self, voice_id: str, cache_dir: Path, render, max_files: int = MAX_CACHE_FILES) -> None:
        self._voice_id = voice_id
        self._dir = cache_dir
        self._render = render
        self._max_files = max_files
        self._lock = threading.Lock()  # one synthesis at a time; Piper is not documented thread-safe
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, text: str) -> Path:
        digest = hashlib.sha256(f"{self._voice_id}\0{text}".encode()).hexdigest()[:32]
        return self._dir / f"tts-{digest}.wav"

    def synthesize(self, text: str) -> Path | None:
        text = text.strip()
        if not text:
            return None
        path = self.path_for(text)
        with self._lock:
            if path.exists():
                path.touch()  # recently used: pruned last
                return path
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".part")
            os.close(fd)
            try:
                with wave.open(tmp, "wb") as wav_file:
                    self._render(text, wav_file)
                os.replace(tmp, path)
            except Exception:
                logger.exception("speech synthesis failed")
                Path(tmp).unlink(missing_ok=True)
                return None
            self._prune()
        return path

    def _prune(self) -> None:
        files = sorted(self._dir.glob("tts-*.wav"), key=lambda p: p.stat().st_mtime)
        for old in files[: max(0, len(files) - self._max_files)]:
            old.unlink(missing_ok=True)


def create_synthesizer(voice: str, cache_dir: Path) -> Synthesizer:
    """A Piper synthesizer for the `.onnx` voice at `voice`, or NoSpeech."""
    if not voice:
        logger.info("no TTS_VOICE set: announcements are clip-only")
        return NoSpeech()
    voice_path = Path(voice)
    if not voice_path.is_file():
        raise ValueError(f"TTS_VOICE {voice!r}: no such file (a Piper .onnx voice, with its .onnx.json next to it)")
    try:
        from piper import PiperVoice
    except ImportError:
        raise ValueError("TTS_VOICE is set but piper-tts is not installed: pip install '.[tts]'") from None
    piper_voice = PiperVoice.load(str(voice_path))
    logger.info("speech: piper voice %s", voice_path.name)
    return CachedSynthesizer(voice_path.stem, cache_dir, piper_voice.synthesize_wav)


def write_silence(path: Path, seconds: float, rate: int = 22050) -> Path:
    """A mono 16-bit WAV of silence — the pre-roll that wakes a sleeping speaker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\0\0" * int(rate * seconds))
    return path
