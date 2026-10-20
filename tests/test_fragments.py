"""The micro-frontend contract with homerun2-portal: a manifest and fragments.

The portal loads each fragment the manifest lists into its own page, on its
own origin. So every URL in a fragment must carry BASE_PATH, the fragment must
be a bare partial, and everything from a message must be escaped.
"""

from __future__ import annotations

import re

import pytest
from conftest import FakePlayer
from fastapi.testclient import TestClient

from sound_catcher.config.settings import ApiConfig
from sound_catcher.history import Announcement, History
from sound_catcher.models import Message
from sound_catcher.web.app import create_app
from sound_catcher.web.fragments import ANNOUNCEMENTS_LIMIT, render_announcements_fragment

AUTH = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def client(make_dispatcher):
    dispatcher, history = make_dispatcher(player=FakePlayer())
    app = create_app(dispatcher, history, ApiConfig(token="s3cret"), version="v1.2.3", base_path="/sound")
    return TestClient(app), dispatcher


def test_manifest_lists_the_announcements_fragment_under_the_base_path(client):
    c, _ = client
    resp = c.get("/sound/ui/manifest.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Sound Catcher"
    assert body["version"] == "v1.2.3"
    assert body["basePath"] == "/sound"
    assert body["fragments"] == [
        {
            "id": "announcements",
            "title": "Latest announcements",
            "url": "/sound/ui/fragment/announcements",
            "refreshSeconds": 5,
        }
    ]
    assert c.get("/ui/manifest.json").status_code == 404


def test_manifest_at_the_root_without_a_base_path(make_dispatcher):
    dispatcher, history = make_dispatcher()
    c = TestClient(create_app(dispatcher, history, ApiConfig()))
    assert c.get("/ui/manifest.json").json()["fragments"][0]["url"] == "/ui/fragment/announcements"
    assert c.get("/ui/fragment/announcements").status_code == 200


def test_fragment_is_a_bare_escaped_partial(client):
    c, d = client
    d.handle_message(Message(system="ci<x>", severity="error", title='build <failed> & "gone"'))
    resp = c.get("/sound/ui/fragment/announcements")
    assert resp.status_code == 200
    page = resp.text
    assert "<html" not in page and "<head" not in page and "<script" not in page
    assert 'class="hr-list"' in page
    assert 'class="hr-sev hr-sev-error"' in page
    assert "ci&lt;x&gt;" in page
    assert "build &lt;failed&gt; &amp; &quot;gone&quot;" in page
    # Every same-origin URL carries the prefix: the fragment runs on the portal's origin.
    urls = re.findall(r'(?:href|src|hx-get|hx-post)="(/[^"]*)"', page)
    assert urls == ["/sound/"]


def test_fragment_lists_what_post_play_produced(client):
    c, _ = client
    assert (
        c.post("/sound/play", json={"say": "Coffee is ready", "severity": "success"}, headers=AUTH).status_code == 200
    )
    page = c.get("/sound/ui/fragment/announcements").text
    assert "Coffee is ready" in page
    assert 'class="hr-sev hr-sev-success"' in page
    assert '<span class="hr-system">api</span>' in page
    assert '<span class="hr-muted">played</span>' in page


def test_fragment_shows_the_mute_state(client):
    c, _ = client
    assert "Muted until" not in c.get("/sound/ui/fragment/announcements").text
    c.put("/sound/mute", json={"minutes": 15}, headers=AUTH)
    c.post("/sound/play", json={"say": "Quiet please"}, headers=AUTH)
    page = c.get("/sound/ui/fragment/announcements").text
    assert '<p class="hr-muted">Muted until ' in page
    assert '<span class="hr-muted">muted</span>' in page


def test_fragment_is_capped_and_newest_first():
    history = History(50)
    for i in range(ANNOUNCEMENTS_LIMIT + 3):
        history.record(Announcement(system="s", title=f"entry {i} ", at=1_000_000.0 + i))
    page = render_announcements_fragment(history.since()[:ANNOUNCEMENTS_LIMIT])
    assert page.count('<li class="hr-item">') == ANNOUNCEMENTS_LIMIT
    assert page.index(f"entry {ANNOUNCEMENTS_LIMIT + 2} ") < page.index(f"entry {ANNOUNCEMENTS_LIMIT + 1} ")
    assert "entry 0 " not in page


def test_route_caps_the_list(make_dispatcher):
    dispatcher, history = make_dispatcher(history=History(50))
    for i in range(ANNOUNCEMENTS_LIMIT + 5):
        history.record(Announcement(system="s", title=f"t{i}"))
    c = TestClient(create_app(dispatcher, history, ApiConfig(), base_path="/sound"))
    assert c.get("/sound/ui/fragment/announcements").text.count('<li class="hr-item">') == ANNOUNCEMENTS_LIMIT


def test_empty_fragment_says_so():
    page = render_announcements_fragment([], base_path="/sound")
    assert '<li class="hr-item hr-muted">No announcements yet.</li>' in page
    assert 'href="/sound/"' in page


@pytest.mark.parametrize(("severity", "css"), [("critical", "error"), ("<weird>", "info"), ("WARNING", "warning")])
def test_severity_maps_onto_the_portal_classes(severity, css):
    page = render_announcements_fragment([Announcement(severity=severity, title="x")])
    assert f"hr-sev hr-sev-{css}" in page
    assert "<weird>" not in page.lower()


def test_replay_is_labelled():
    page = render_announcements_fragment([Announcement(title="x", origin="replay")])
    assert '<span class="hr-muted">replayed</span>' in page
