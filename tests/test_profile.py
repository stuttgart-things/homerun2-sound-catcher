from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from sound_catcher.models import Message
from sound_catcher.profile import QuietHours, load_profile, parse_profile


def test_first_match_wins_and_filters_combine():
    profile = parse_profile(
        {
            "rules": {
                "tagged": {"systems": ["ci"], "tags": ["prod"], "say": "prod"},
                "ci": {"systems": ["ci"], "severity": ["error"], "say": "ci"},
                "any": {"systems": ["*"], "say": "any"},
            }
        }
    )
    assert profile.match(Message(system="ci", tags="prod, nightly")).name == "tagged"
    assert profile.match(Message(system="ci", severity="ERROR")).name == "ci"
    assert profile.match(Message(system="ci", severity="info")).name == "any"


def test_render_flattens_and_caps_text():
    profile = parse_profile({"rules": {"r": {"say": "{{ title }}\n\n{{ message }}"}}})
    rule = profile.rules[0]
    assert rule.render(Message(title="a", message="b   c")) == "a b c"
    assert len(rule.render(Message(title="x" * 2000))) == 500


def test_render_failure_is_empty_not_an_exception():
    rule = parse_profile({"rules": {"r": {"say": "{{ nope }}"}}}).rules[0]
    assert rule.render(Message()) == ""


def test_sandbox_blocks_attribute_tricks():
    rule = parse_profile({"rules": {"r": {"say": "{{ title.__class__.__mro__ }}"}}}).rules[0]
    assert rule.render(Message(title="x")) == ""


@pytest.mark.parametrize(
    "raw, error",
    [
        ({"sound": "../etc/passwd"}, "file name"),
        ({"sound": "a.wav", "volume": 101}, "volume"),
        ({"systems": ["x"]}, "needs sound"),
        ({"say": "{% if %}"}, "valid template"),
    ],
)
def test_invalid_rules_fail_loading(raw, error):
    with pytest.raises(ValueError, match=error):
        parse_profile({"rules": {"bad": raw}})


def test_quiet_hours_cross_midnight_and_weekends():
    zone = ZoneInfo("Europe/Berlin")
    q = QuietHours(start=time(18), end=time(8), weekends=True, timezone="Europe/Berlin", allow=["critical"])
    thursday = datetime(2026, 10, 15, tzinfo=zone)
    assert q.active(thursday.replace(hour=22))
    assert q.active(thursday.replace(hour=7, minute=59))
    assert not q.active(thursday.replace(hour=12))
    assert q.active(datetime(2026, 10, 17, 12, tzinfo=zone))  # Saturday
    assert q.silences("error", thursday.replace(hour=22))
    assert not q.silences("critical", thursday.replace(hour=22))


def test_unquoted_yaml_times(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("quietHours:\n  from: 19:00\n  to: 07:30\nrules: {}\n")
    q = load_profile(path).quiet_hours
    assert (q.start, q.end) == (time(19), time(7, 30))


def test_missing_profile_is_empty(tmp_path):
    assert load_profile(tmp_path / "nope.yaml").rules == []


def test_bundled_profile_loads_and_names_bundled_clips():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    profile = load_profile(root / "profile.yaml")
    assert profile.rules and profile.quiet_hours
    for rule in profile.rules:
        if rule.sound:
            assert (root / "sounds" / rule.sound).is_file(), rule.sound


@pytest.mark.parametrize(
    "title, spoken",
    [
        ("[WARN] k8s-cluster disk usage above 80%", "k8s-cluster disk usage above 80%"),
        ("[OK] [CI] redis pipeline passed", "redis pipeline passed"),
        ("disk [80%] full", "disk [80%] full"),
        ("[WARN]", "[WARN]"),
    ],
)
def test_spoken_title_drops_leading_tags(title, spoken):
    from sound_catcher.profile.engine import spoken_title

    assert spoken_title(title) == spoken


def test_default_profile_speaks_english_without_repeating_the_system():
    from pathlib import Path

    profile = load_profile(Path(__file__).resolve().parents[1] / "profile.yaml")
    warn = Message(system="k8s-cluster", severity="warning", title="[WARN] k8s-cluster disk usage above 80%")
    assert profile.match(warn).render(warn) == "Warning: k8s-cluster disk usage above 80%"
    err = Message(system="nas01", severity="error", title="backup failed")
    assert profile.match(err).render(err) == "Error on nas01: backup failed"
