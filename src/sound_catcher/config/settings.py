"""Configuration loaded from environment variables, once at startup."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_REDIS_STARTUP_TIMEOUT = 120.0
"""Seconds the consumer retries Redis at startup before the process exits."""

DEFAULT_MAX_MESSAGE_AGE = 60.0
"""Entries older than this are acknowledged unplayed, as in light-catcher.
For sound this matters more than for lights: a backlog read out loud after a
restart is the fastest way to get the speaker unplugged."""

SOUND_MODES = ("full", "standalone")
"""`standalone` runs without Redis; only POST /play makes a sound."""

SINKS = ("local", "browser")
"""Where audio goes: `local` plays on this machine's audio output (the office
speaker), `browser` sends it to every open player page."""


@dataclass
class RedisConfig:
    addr: str = "localhost"
    port: int = 6379
    password: str = ""
    streams: list[str] = field(default_factory=lambda: ["messages"])


@dataclass
class ApiConfig:
    """The write API: POST /play, speaker replay, mute.

    No token means no write API: the endpoints are not registered at all. It
    makes noise in a room full of people, so it fails closed.
    """

    token: str = ""
    max_text: int = 300
    rate_limit: int = 30  # writes per minute, across all callers

    @property
    def writable(self) -> bool:
        return bool(self.token)


@dataclass
class Config:
    redis: RedisConfig = field(default_factory=RedisConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    mode: str = "full"
    sinks: list[str] = field(default_factory=lambda: list(SINKS))
    consumer_group: str = "homerun2-sound-catcher"
    consumer_name: str = ""
    consumer_start_id: str = "$"
    max_message_age: float = DEFAULT_MAX_MESSAGE_AGE
    redis_startup_timeout: float = DEFAULT_REDIS_STARTUP_TIMEOUT
    health_port: int = 8080
    profile_path: str = "profile.yaml"
    sounds_dir: Path = REPO_ROOT / "sounds"
    tts_voice: str = ""  # path to a Piper .onnx voice; empty: no speech
    tts_cache_dir: Path = Path.home() / ".cache" / "homerun2-sound-catcher"
    player_cmd: str = ""  # empty: the first of mpv, ffplay, pw-play found
    preroll: float = 0.5  # seconds of silence before each local announcement
    dedup_window: float = 60.0  # seconds; 0 = off
    history_size: int = 20
    log_format: str = "json"
    log_level: str = "info"
    version: str = "dev"
    commit: str = "unknown"
    date: str = "unknown"

    @property
    def local(self) -> bool:
        return "local" in self.sinks

    @property
    def browser(self) -> bool:
        return "browser" in self.sinks


def _getenv(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """An integer from the environment, or `default` when unset. A typo fails startup."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{key} {raw!r}: must be an integer") from None
    if minimum is not None and value < minimum:
        raise ValueError(f"{key} {raw!r}: must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{key} {raw!r}: must be at most {maximum}")
    return value


def _env_duration(key: str, default: float) -> float:
    """A Go-style duration from the environment; ``0`` turns the feature off."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    if raw == "0":
        return 0.0
    try:
        seconds = parse_duration(raw)
    except ValueError as exc:
        raise ValueError(f"{key} {raw!r}: {exc}") from None
    if seconds < 0:
        raise ValueError(f"{key} {raw!r}: must not be negative")
    return seconds


def parse_streams(streams_env: str, stream_fallback: str) -> list[str]:
    """REDIS_STREAMS (comma-separated) wins over the legacy REDIS_STREAM."""
    parts = [p.strip() for p in streams_env.split(",") if p.strip()]
    if parts:
        return parts
    return [stream_fallback.strip() or "messages"]


def parse_sinks(raw: str) -> list[str]:
    """``SOUND_SINKS``: comma-separated, from ``local`` and ``browser``."""
    if not raw.strip():
        return list(SINKS)
    sinks: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in SINKS:
            raise ValueError(f"SOUND_SINKS {raw!r}: unknown sink {name!r}, must be from {', '.join(SINKS)}")
        if name not in sinks:
            sinks.append(name)
    return sinks


def parse_mode(raw: str) -> str:
    mode = raw.strip().lower() or "full"
    if mode not in SOUND_MODES:
        raise ValueError(f"SOUND_MODE {raw!r}: must be one of {', '.join(SOUND_MODES)}")
    return mode


_DURATION_PART = re.compile(r"(\d+(?:\.\d*)?|\.\d+)(ns|us|µs|ms|s|m|h)")
_DURATION_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str) -> float:
    """Parse a Go-style duration (``90s``, ``2m``, ``1m30s``, ``500ms``) into seconds.

    Same syntax as Go's ``time.ParseDuration``, so durations read the same here
    as in the Go homerun2 services. A bare number has no unit and is rejected.
    """
    text = value.strip()
    sign = 1.0
    if text[:1] in ("+", "-"):
        sign = -1.0 if text[0] == "-" else 1.0
        text = text[1:]
    if not text:
        raise ValueError(f"invalid duration {value!r}")
    if text == "0":
        return 0.0
    total = 0.0
    pos = 0
    for match in _DURATION_PART.finditer(text):
        if match.start() != pos:
            raise ValueError(f"invalid duration {value!r}")
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        pos = match.end()
    if pos != len(text) or pos == 0:
        raise ValueError(f"invalid duration {value!r}")
    return sign * total


def load_config() -> Config:
    timeout = _env_duration("REDIS_STARTUP_TIMEOUT", DEFAULT_REDIS_STARTUP_TIMEOUT)
    if timeout <= 0:
        raise ValueError("REDIS_STARTUP_TIMEOUT: must be positive")
    cache_dir = _getenv("TTS_CACHE_DIR").strip()
    sounds_dir = _getenv("SOUNDS_DIR").strip()
    return Config(
        redis=RedisConfig(
            addr=_getenv("REDIS_ADDR", "localhost"),
            port=_env_int("REDIS_PORT", 6379, minimum=1, maximum=65535),
            password=_getenv("REDIS_PASSWORD"),
            streams=parse_streams(_getenv("REDIS_STREAMS"), _getenv("REDIS_STREAM")),
        ),
        api=ApiConfig(
            token=_getenv("SOUND_API_TOKEN").strip(),
            max_text=_env_int("SOUND_API_MAX_TEXT", 300, minimum=1),
            rate_limit=_env_int("SOUND_API_RATE_LIMIT", 30, minimum=1),
        ),
        mode=parse_mode(_getenv("SOUND_MODE")),
        sinks=parse_sinks(_getenv("SOUND_SINKS")),
        consumer_group=_getenv("CONSUMER_GROUP").strip() or "homerun2-sound-catcher",
        consumer_name=_getenv("CONSUMER_NAME").strip() or socket.gethostname(),
        consumer_start_id=_getenv("CONSUMER_START_ID").strip() or "$",
        max_message_age=_env_duration("MAX_MESSAGE_AGE", DEFAULT_MAX_MESSAGE_AGE),
        redis_startup_timeout=timeout,
        health_port=_env_int("HEALTH_PORT", 8080, minimum=1, maximum=65535),
        profile_path=_getenv("PROFILE_PATH", "profile.yaml"),
        sounds_dir=Path(sounds_dir) if sounds_dir else REPO_ROOT / "sounds",
        tts_voice=_getenv("TTS_VOICE").strip(),
        tts_cache_dir=Path(cache_dir) if cache_dir else Path.home() / ".cache" / "homerun2-sound-catcher",
        player_cmd=_getenv("PLAYER_CMD").strip(),
        preroll=_env_duration("PREROLL", 0.5),
        dedup_window=_env_duration("DEDUP_WINDOW", 60.0),
        history_size=_env_int("HISTORY_SIZE", 20, minimum=1, maximum=500),
        log_format=_getenv("LOG_FORMAT", "json"),
        log_level=_getenv("LOG_LEVEL", "info"),
        version=_getenv("VERSION", "dev"),
        commit=_getenv("COMMIT", "unknown"),
        date=_getenv("DATE", "unknown"),
    )


def setup_logging(cfg: Config) -> None:
    level = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
    }.get(cfg.log_level.lower(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    if cfg.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


_EXTRA_KEYS = (
    "version",
    "commit",
    "date",
    "mode",
    "sinks",
    "redis_addr",
    "streams",
    "consumer_group",
    "stream",
    "stream_id",
    "object_id",
    "severity",
    "system",
    "title",
    "author",
    "outcome",
    "error",
    "attempt",
    "attempts",
    "next_sleep",
)


class _JsonFormatter(logging.Formatter):
    """Structured JSON log formatter matching Go slog output style."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        for key in _EXTRA_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)
