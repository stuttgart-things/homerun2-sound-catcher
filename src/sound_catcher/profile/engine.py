"""YAML sound profile: which message gets which clip and which announcement.

Rules are tried in file order; the first whose systems, severities and tags
all fit wins. A rule plays a clip from ``SOUNDS_DIR``, speaks a Jinja
template, or both (clip first — a chime gets people listening before the
voice starts). ``silent: true`` matches and plays nothing, to keep a noisy
system off the speaker without falling through to a broader rule.

``quietHours`` works as in led-catcher: inside the window only the ``allow``
severities make a sound.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from sound_catcher.models import Message

logger = logging.getLogger(__name__)

MAX_SPOKEN_CHARS = 500
"""A rendered announcement is cut here. A message body pasted into `say` should
not keep the office listening for a minute."""

_TAG_PREFIX = re.compile(r"^\s*(?:\[[^\]\n]{1,24}\]\s*)+")


def spoken_title(title: str) -> str:
    """The title without leading tags like ``[WARN]`` or ``[OK]``: they are for
    eyes, and a voice reading out "bracket warn" is noise. The severity is
    in the rule's own words anyway."""
    return _TAG_PREFIX.sub("", title).strip() or title.strip()


_jinja = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)  # nosec B701 - spoken text, not HTML


def is_bare_name(name: str) -> bool:
    """A file name with no directory part — what rules and the API may name."""
    return bool(name) and name not in (".", "..") and Path(name).name == name and "\\" not in name


@dataclass
class SoundRule:
    name: str
    systems: list[str] = field(default_factory=list)  # empty: any system
    severity: list[str] = field(default_factory=list)  # empty: any severity
    tags: list[str] = field(default_factory=list)  # every one must be among the message's tags
    sound: str = ""  # a clip in SOUNDS_DIR
    say: str = ""  # a Jinja template over the message fields
    volume: int = 100
    silent: bool = False

    def matches(self, msg: Message) -> bool:
        if self.systems and msg.system not in self.systems and "*" not in self.systems:
            return False
        if self.severity and msg.severity.lower() not in self.severity:
            return False
        msg_tags = {t.strip() for t in msg.tags.split(",")} if msg.tags else set()
        return all(tag in msg_tags for tag in self.tags)

    def render(self, msg: Message) -> str:
        """The announcement text, or "" when the rule has none or it does not render."""
        if not self.say:
            return ""
        try:
            text = _jinja.from_string(self.say).render(
                title=msg.title,
                spoken_title=spoken_title(msg.title),
                message=msg.message,
                severity=msg.severity,
                system=msg.system,
                author=msg.author,
                tags=msg.tags,
                url=msg.url,
            )
        except TemplateError as exc:
            logger.warning("rule %s: say template failed: %s", self.name, exc)
            return ""
        return " ".join(text.split())[:MAX_SPOKEN_CHARS]


@dataclass
class QuietHours:
    """When only the `allow` severities make a sound. `from`–`to` may cross midnight."""

    start: time
    end: time
    weekends: bool = False
    timezone: str = ""
    allow: list[str] = field(default_factory=lambda: ["critical"])

    def active(self, now: datetime | None = None) -> bool:
        zone = self._zone()
        local = (now or datetime.now(zone)).astimezone(zone)
        if self.weekends and local.weekday() >= 5:
            return True
        t = local.time()
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end

    def silences(self, severity: str, now: datetime | None = None) -> bool:
        return severity.lower() not in self.allow and self.active(now)

    def _zone(self):
        if not self.timezone:
            return None
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            logger.warning("quietHours timezone %s not found, using local time", self.timezone)
            return None


@dataclass
class Profile:
    rules: list[SoundRule] = field(default_factory=list)
    quiet_hours: QuietHours | None = None

    def match(self, msg: Message) -> SoundRule | None:
        return next((rule for rule in self.rules if rule.matches(msg)), None)


def load_profile(path: str | Path, rules_expected: bool = True) -> Profile:
    """Load the profile. A missing file is an empty profile: nothing matches,
    so nothing from the stream plays — logged loudly where that matters."""
    path = Path(path)
    if not path.exists():
        log = logger.warning if rules_expected else logger.info
        log("profile %s not found, no rules loaded", path)
        return Profile()
    data = yaml.safe_load(path.read_text()) or {}
    return parse_profile(data)


def parse_profile(data: dict) -> Profile:
    """A profile from its YAML structure. Raises ValueError on an invalid rule."""
    rules = []
    for name, raw in (data.get("rules") or {}).items():
        raw = raw or {}
        rule = SoundRule(
            name=str(name),
            systems=[str(s) for s in raw.get("systems", [])],
            severity=[str(s).lower() for s in raw.get("severity", [])],
            tags=[str(t) for t in raw.get("tags", [])],
            sound=str(raw.get("sound", "")),
            say=str(raw.get("say", "")),
            volume=int(raw.get("volume", 100)),
            silent=bool(raw.get("silent", False)),
        )
        if rule.sound and not is_bare_name(rule.sound):
            raise ValueError(f"rule {name}: sound must be a file name in SOUNDS_DIR, not a path")
        if not 0 <= rule.volume <= 100:
            raise ValueError(f"rule {name}: volume must be 0-100")
        if not (rule.sound or rule.say or rule.silent):
            raise ValueError(f"rule {name}: needs sound, say, or silent: true")
        if rule.say:
            try:
                _jinja.parse(rule.say)
            except TemplateError as exc:
                raise ValueError(f"rule {name}: say is not a valid template: {exc}") from None
        rules.append(rule)
    return Profile(rules=rules, quiet_hours=_parse_quiet_hours(data.get("quietHours")))


def _parse_quiet_hours(data: dict | None) -> QuietHours | None:
    if not data:
        return None
    return QuietHours(
        start=_clock_time(data["from"]),
        end=_clock_time(data["to"]),
        weekends=bool(data.get("weekends", False)),
        timezone=str(data.get("timezone", "")),
        allow=[str(s).lower() for s in data.get("allow", ["critical"])],
    )


def _clock_time(value: str | int) -> time:
    """`HH:MM`. Unquoted, YAML 1.1 reads 19:00 as 1140 (minutes), so an int is minutes."""
    if isinstance(value, int):
        return time(value // 60, value % 60)
    return time.fromisoformat(str(value))
