from __future__ import annotations

import asyncio
import json

import pytest
from conftest import FakePlayer
from fastapi.testclient import TestClient

from sound_catcher.config.settings import ApiConfig
from sound_catcher.models import Message
from sound_catcher.web.app import RateLimiter, create_app, event_stream

AUTH = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def setup(make_dispatcher):
    def _setup(token: str = "s3cret", player=None, rate_limit: int = 30, history_size: int = 20):
        dispatcher, history = make_dispatcher(player=player if player is not None else FakePlayer())
        app = create_app(dispatcher, history, ApiConfig(token=token, rate_limit=rate_limit), history_size=history_size)
        return TestClient(app), dispatcher, history

    return _setup


def test_page_and_state(setup):
    client, _, _ = setup()
    page = client.get("/")
    assert page.status_code == 200 and "Ton aktivieren" in page.text
    state = client.get("/api/state").json()
    assert state["sinks"] == ["local", "browser"]
    assert state["speakerReplay"] is True
    assert state["serverTime"] > 0


def test_history_since_session_start(setup, clock):
    client, d, history = setup()
    d.handle_message(Message(system="a", severity="error", title="old"))
    clock.now += 100
    newer = d.handle_message(Message(system="b", severity="error", title="new"))
    # History entries carry wall-clock time; filter on the recorded one.
    old_at = history.since()[-1].at
    body = client.get("/api/history", params={"since": old_at + 0.000001}).json()
    assert [e["id"] for e in body] == [e.id for e in history.since(old_at + 0.000001)]
    assert client.get("/api/history", params={"after": newer.id - 1}).json()[0]["title"] == "new"


def test_history_is_capped(setup):
    client, d, _ = setup()
    d._history._entries = type(d._history._entries)(maxlen=3)
    for i in range(5):
        d.handle_message(Message(system="s", severity="error", title=f"t{i}"))
    titles = [e["title"] for e in client.get("/api/history").json()]
    assert titles == ["t4", "t3", "t2"]


def test_audio_is_served_by_entry_not_path(setup):
    client, d, _ = setup()
    entry = d.handle_message(Message(system="nas", severity="error", title="x"))
    assert client.get(f"/audio/{entry.id}/0").status_code == 200
    assert client.get(f"/audio/{entry.id}/1").headers["content-type"] == "audio/wav"
    assert client.get(f"/audio/{entry.id}/2").status_code == 404
    assert client.get("/audio/999/0").status_code == 404
    assert client.get("/audio/..%2F..%2Fetc/passwd").status_code == 404


def test_write_endpoints_need_the_token(setup):
    client, _, _ = setup()
    assert client.post("/play", json={"say": "hi"}).status_code == 401
    assert client.post("/play", json={"say": "hi"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.put("/mute", json={"minutes": 5}).status_code == 401


def test_without_token_write_endpoints_do_not_exist(setup):
    client, _, _ = setup(token="")
    assert client.post("/play", json={"say": "hi"}, headers=AUTH).status_code in (404, 405)
    assert client.get("/api/state").json()["writable"] is False
    assert client.get("/mute").status_code == 200


def test_play(setup):
    client, _, history = setup()
    res = client.post("/play", json={"say": "Pizza ist da", "sound": "chime.wav", "volume": 50}, headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["outcome"] == "played" and len(body["audio"]) == 2 and body["autoplay"]
    assert history.get(body["id"]).origin == "api"


@pytest.mark.parametrize(
    "payload",
    [{}, {"sound": "../x.wav"}, {"say": "x" * 301}, {"say": "a", "volume": 101}, {"say": "a", "extra": 1}],
)
def test_play_rejects_bad_requests(setup, payload):
    client, _, _ = setup()
    assert client.post("/play", json=payload, headers=AUTH).status_code == 422


def test_rate_limit(setup):
    client, _, _ = setup(rate_limit=2)
    for _ in range(2):
        assert client.post("/play", json={"say": "a"}, headers=AUTH).status_code == 200
    res = client.post("/play", json={"say": "a"}, headers=AUTH)
    assert res.status_code == 429 and int(res.headers["Retry-After"]) >= 1


def test_mute_endpoints(setup):
    client, d, _ = setup()
    assert client.put("/mute", json={"minutes": 15}, headers=AUTH).json()["mutedUntil"]
    assert client.get("/mute").json()["mutedUntil"]
    assert client.post("/play", json={"say": "a"}, headers=AUTH).json()["outcome"] == "muted"
    assert client.put("/mute", json={"minutes": 0}, headers=AUTH).status_code == 422
    assert client.delete("/mute", headers=AUTH).json() == {"mutedUntil": None}
    assert d.muted_until == 0


def test_speaker_replay(setup):
    player = FakePlayer()
    client, d, _ = setup(player=player)
    entry = d.handle_message(Message(system="nas", severity="error", title="x"))
    res = client.post(f"/api/history/{entry.id}/replay", headers=AUTH)
    assert res.status_code == 200 and res.json()["replayOf"] == entry.id
    assert res.json()["autoplay"] is False  # the speaker replays it, not every open page
    assert len(player.jobs) == 2
    assert client.post("/api/history/999/replay", headers=AUTH).status_code == 404


def test_no_speaker_replay_without_local_sink(make_dispatcher):
    d, history = make_dispatcher(player=None)
    client = TestClient(create_app(d, history, ApiConfig(token="s3cret")))
    assert client.get("/api/state").json()["speakerReplay"] is False
    assert client.post("/api/history/1/replay", headers=AUTH).status_code == 404


def test_health(setup):
    client, _, _ = setup()
    assert client.get("/healthz").json()["status"] == "ok"
    d, history = setup()[1:]
    unhealthy = TestClient(create_app(d, history, ApiConfig(), health=lambda: {"status": "unhealthy"}))
    assert unhealthy.get("/healthz").status_code == 503


def test_rate_limiter_window():
    now = [0.0]
    limiter = RateLimiter(1, window=60, clock=lambda: now[0])
    assert limiter.acquire() == 0
    assert limiter.acquire() == 60
    now[0] = 60
    assert limiter.acquire() == 0


async def test_event_stream_sends_state_then_new_announcements_once(make_dispatcher):
    d, history = make_dispatcher()
    d.handle_message(Message(system="before", severity="error", title="before connect"))
    stop = asyncio.Event()
    state = {"mutedUntil": None}

    async def connected():
        return False

    stream = event_stream(history, connected, lambda: dict(state), interval=0.01, stop=stop)
    first = await anext(stream)
    assert first["event"] == "state"
    d.handle_message(Message(system="a", severity="error", title="one"))
    d.handle_message(Message(system="b", severity="error", title="two"))
    got = [await anext(stream), await anext(stream)]
    assert [json.loads(e["data"])["title"] for e in got] == ["one", "two"]
    state["mutedUntil"] = "soon"
    assert (await anext(stream))["event"] == "state"
    stop.set()
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


def test_sounds_listing_and_unknown_sound(setup):
    client, _, _ = setup()
    sounds = client.get("/api/sounds").json()
    assert sounds == {"alarm.wav": "bundled", "chime.wav": "bundled", "success.wav": "bundled"}
    res = client.post("/play", json={"sound": "nope.wav"}, headers=AUTH)
    assert res.status_code == 422 and "api/sounds" in res.json()["detail"]
