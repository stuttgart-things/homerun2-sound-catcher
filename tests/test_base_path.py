"""BASE_PATH: the player behind homerun2-portal under a prefix such as /sound.

The portal forwards the path unchanged, so the app answers under the prefix
and every URL the page uses carries it; /health and /healthz stay at the root.
"""

from __future__ import annotations

import re

import pytest
from conftest import FakePlayer
from fastapi.testclient import TestClient

from sound_catcher.config.settings import ApiConfig, load_config, parse_base_path
from sound_catcher.models import Message
from sound_catcher.web.app import create_app

AUTH = {"Authorization": "Bearer s3cret"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", ""), ("/", ""), ("/sound", "/sound"), ("sound/", "/sound"), (" /homerun/sound ", "/homerun/sound")],
)
def test_parse_base_path(raw, expected):
    assert parse_base_path(raw) == expected


@pytest.mark.parametrize("raw", ["/a//b", "/../sound", '/sound"', "/sound?x=1", "/sound</script>"])
def test_parse_base_path_rejects_anything_but_plain_segments(raw):
    with pytest.raises(ValueError, match="BASE_PATH"):
        parse_base_path(raw)


def test_load_config_reads_base_path(monkeypatch):
    monkeypatch.setenv("BASE_PATH", "/sound/")
    assert load_config().base_path == "/sound"


@pytest.fixture
def client(make_dispatcher):
    dispatcher, history = make_dispatcher(player=FakePlayer())
    app = create_app(dispatcher, history, ApiConfig(token="s3cret"), base_path="/sound")
    return TestClient(app), dispatcher


def test_routes_under_the_base_path(client):
    c, _ = client
    assert c.get("/sound/").status_code == 200
    assert c.get("/sound/api/state").status_code == 200
    assert c.get("/sound/api/history").status_code == 200
    assert c.get("/sound/mute").status_code == 200
    assert c.put("/sound/mute", json={"minutes": 1}, headers=AUTH).status_code == 200
    for path in ("/health", "/healthz"):
        assert c.get(path).json()["status"] == "ok"
    for path in ("/", "/sound"):
        resp = c.get(path, follow_redirects=False)
        assert resp.status_code == 307 and resp.headers["location"] == "/sound/"
    assert c.get("/api/state").status_code == 404


def test_audio_urls_carry_the_base_path(client):
    c, d = client
    entry = d.handle_message(Message(system="s", severity="error", title="t"))
    body = c.get("/sound/api/history").json()
    assert body[0]["audio"] and all(url.startswith("/sound/audio/") for url in body[0]["audio"])
    assert c.get(body[0]["audio"][0]).status_code == 200
    replay = c.post(f"/sound/api/history/{entry.id}/replay", headers=AUTH).json()
    assert all(url.startswith("/sound/audio/") for url in replay["audio"])


# Same-origin URLs the page hands to the browser itself: URL attributes, and
# string literals passed straight to fetch() or EventSource. write() takes a
# path and joins it to BASE_PATH, checked separately below.
_ROOT_URL = re.compile(r"""(?:href|src|action)="(/(?!/)[^"]*)"|(?:fetch|EventSource)\(\s*["'`](/[^"'`]*)""")


def _unprefixed(page: str) -> list[str]:
    urls = [attr or literal for attr, literal in _ROOT_URL.findall(page)]
    return [url for url in urls if not url.startswith("/sound/")]


def test_page_points_every_url_at_the_base_path(client):
    c, _ = client
    page = c.get("/sound/").text
    assert "{{" not in page
    assert 'const BASE_PATH = "/sound";' in page
    assert 'new EventSource(BASE_PATH + "/api/events")' in page
    assert "fetch(BASE_PATH + path," in page
    assert not _unprefixed(page)


def test_without_a_base_path_the_page_is_unchanged(make_dispatcher):
    dispatcher, history = make_dispatcher(player=FakePlayer())
    c = TestClient(create_app(dispatcher, history, ApiConfig()))
    page = c.get("/").text
    assert 'const BASE_PATH = "";' in page
    assert c.get("/health").status_code == 200
    d = dispatcher.handle_message(Message(system="s", severity="error", title="t"))
    assert all(url.startswith("/audio/") for url in d.to_json()["audio"])
