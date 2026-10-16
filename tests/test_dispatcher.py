from __future__ import annotations

from datetime import time

from conftest import FakePlayer

from sound_catcher.models import Message
from sound_catcher.profile import QuietHours


def msg(**kw) -> Message:
    return Message(**{"system": "nas01", "severity": "error", "title": "disk full", **kw})


def test_rule_plays_clip_then_speech_on_both_sinks(make_dispatcher):
    player = FakePlayer()
    d, history = make_dispatcher(player=player)
    entry = d.handle_message(msg(system="backup", title="nightly"))
    assert entry.outcome == "played"
    assert entry.rule == "backup"
    assert entry.text == "Backup auf backup fehlgeschlagen: nightly"
    assert [f.name for f in entry.files][0] == "alarm.wav"
    assert entry.files[1].name.startswith("tts-")
    assert entry.sinks == ["browser", "local"]
    assert player.jobs[0].volume == 90
    assert history.get(entry.id) is entry


def test_no_rule_and_silent_rule_are_recorded_but_not_played(make_dispatcher):
    player = FakePlayer()
    d, history = make_dispatcher(player=player)
    assert d.handle_message(msg(severity="warning")).outcome == "no_rule"
    silent = d.handle_message(msg(severity="info"))
    assert silent.outcome == "silent"
    assert player.jobs == []
    assert len(history.since()) == 2
    assert not any(e.to_json()["autoplay"] for e in history.since())


def test_repeat_within_window_is_heard_once(make_dispatcher, clock):
    player = FakePlayer()
    d, _ = make_dispatcher(player=player)
    assert d.handle_message(msg()).outcome == "played"
    clock.now += 30
    repeat = d.handle_message(msg())
    assert repeat.outcome == "duplicate"
    # Still listenable on demand.
    assert repeat.files and repeat.to_json()["audio"]
    clock.now += 61
    assert d.handle_message(msg()).outcome == "played"
    assert len(player.jobs) == 2


def test_dedup_off(make_dispatcher):
    d, _ = make_dispatcher(dedup=0)
    assert d.handle_message(msg()).outcome == "played"
    assert d.handle_message(msg()).outcome == "played"


def test_mute_silences_until_it_ends(make_dispatcher, clock):
    player = FakePlayer()
    d, _ = make_dispatcher(player=player)
    d.mute(15 * 60)
    assert d.muted_until == clock.now + 900
    assert d.handle_message(msg()).outcome == "muted"
    clock.now += 901
    assert d.muted_until == 0
    assert d.handle_message(msg(title="other")).outcome == "played"
    d.mute(600)
    d.mute(0)
    assert d.muted_until == 0


def test_quiet_hours(make_dispatcher):
    d, _ = make_dispatcher()
    d._profile.quiet_hours = QuietHours(start=time(0, 0), end=time(23, 59, 59), allow=["critical"])
    assert d.handle_message(msg()).outcome == "quiet_hours"
    assert d.handle_message(msg(severity="critical")).outcome == "played"


def test_missing_clip_without_speech_plays_nothing(make_dispatcher, sounds_dir):
    (sounds_dir / "success.wav").unlink()
    d, _ = make_dispatcher()
    entry = d.handle_message(msg(severity="success"))
    assert entry.outcome == "nothing"
    assert entry.sinks == []


def test_browser_only_and_full_local_queue(make_dispatcher):
    d, _ = make_dispatcher(browser=True, player=FakePlayer(accept=False))
    entry = d.handle_message(msg())
    assert entry.sinks == ["browser"]  # local dropped it, the browser still gets it
    d2, _ = make_dispatcher(browser=False, player=None)
    entry = d2.handle_message(msg())
    assert entry.outcome == "played" and entry.sinks == []
    assert entry.to_json()["autoplay"] is False


def test_announce_respects_mute_unless_forced(make_dispatcher):
    d, _ = make_dispatcher()
    d.mute(60)
    assert d.announce(text="Pizza ist da").outcome == "muted"
    forced = d.announce(text="Pizza ist da", force=True)
    assert forced.outcome == "played"
    assert forced.origin == "api" and forced.title == "Pizza ist da"


def test_replay_goes_to_the_speaker_only_and_ignores_mute(make_dispatcher):
    player = FakePlayer()
    d, history = make_dispatcher(player=player)
    original = d.handle_message(msg())
    d.mute(600)
    again = d.replay(original.id)
    assert again.outcome == "played"
    assert again.replay_of == original.id
    assert again.sinks == ["local"]
    assert len(player.jobs) == 2
    assert d.replay(999) is None


def test_submit_runs_on_the_worker(make_dispatcher):
    from sound_catcher.models import CaughtMessage

    d, history = make_dispatcher()
    entry = d.submit(CaughtMessage(message=msg())).result(timeout=5)
    assert history.get(entry.id) is entry
    d.close()
