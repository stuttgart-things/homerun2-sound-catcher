from __future__ import annotations

import pytest

from sound_catcher.config.settings import load_config, parse_duration, parse_sinks, parse_streams


def test_defaults(monkeypatch):
    for key in ("SOUND_SINKS", "SOUND_MODE", "REDIS_STREAMS", "REDIS_STREAM", "SOUND_API_TOKEN", "HISTORY_SIZE"):
        monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    assert cfg.sinks == ["local", "browser"]
    assert cfg.mode == "full"
    assert cfg.redis.streams == ["messages"]
    assert cfg.consumer_group == "homerun2-sound-catcher"
    assert cfg.history_size == 20
    assert not cfg.api.writable
    assert cfg.preroll == 0.5 and cfg.dedup_window == 60


def test_env(monkeypatch):
    monkeypatch.setenv("SOUND_SINKS", "browser")
    monkeypatch.setenv("SOUND_MODE", "standalone")
    monkeypatch.setenv("DEDUP_WINDOW", "0")
    monkeypatch.setenv("PREROLL", "800ms")
    monkeypatch.setenv("HISTORY_SIZE", "50")
    monkeypatch.setenv("SOUND_API_TOKEN", " secret ")
    cfg = load_config()
    assert cfg.sinks == ["browser"] and not cfg.local and cfg.browser
    assert cfg.mode == "standalone"
    assert cfg.dedup_window == 0
    assert cfg.preroll == pytest.approx(0.8)
    assert cfg.history_size == 50
    assert cfg.api.token == "secret"


@pytest.mark.parametrize(
    "key, value",
    [
        ("SOUND_SINKS", "speaker"),
        ("SOUND_MODE", "led"),
        ("HISTORY_SIZE", "0"),
        ("DEDUP_WINDOW", "60"),
        ("PREROLL", "x"),
    ],
)
def test_typos_fail_startup(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        load_config()


def test_parsers():
    assert parse_sinks("browser, local,browser") == ["browser", "local"]
    assert parse_streams("a, ,b", "c") == ["a", "b"]
    assert parse_streams("", "c") == ["c"]
    assert parse_duration("1m30s") == 90
    with pytest.raises(ValueError):
        parse_duration("90")
