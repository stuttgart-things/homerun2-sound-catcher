"""Decides what a message sounds like, and sends it to the sinks.

For each message: match a rule, check mute, quiet hours and repeats, render
and synthesize the announcement, record it in the history (which is what the
browser sink follows), and queue it on the local speaker.

Messages are handled on one worker thread, in arrival order: synthesis takes
a moment, and the consumer's event loop also serves the web page.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from sound_catcher.audio.library import SoundLibrary
from sound_catcher.audio.player import Job, LocalPlayer
from sound_catcher.audio.tts import NoSpeech, Synthesizer
from sound_catcher.history import PLAYED, Announcement, History
from sound_catcher.models import CaughtMessage, Message
from sound_catcher.profile import Profile

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        profile: Profile,
        history: History,
        sounds: SoundLibrary | Path,
        synthesizer: Synthesizer | None = None,
        player: LocalPlayer | None = None,
        browser: bool = True,
        dedup_window: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._profile = profile
        self._history = history
        self._sounds = SoundLibrary.of(sounds)
        self._tts = synthesizer or NoSpeech()
        self._player = player
        self._browser = browser
        self._dedup_window = dedup_window
        self._clock = clock
        self._recent: dict[tuple, float] = {}
        self._muted_until = 0.0
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dispatch")

    @property
    def sinks(self) -> list[str]:
        return [name for name, on in (("local", self._player is not None), ("browser", self._browser)) if on]

    @property
    def browser(self) -> bool:
        return self._browser

    @property
    def sounds(self) -> SoundLibrary:
        return self._sounds

    @property
    def speech(self) -> bool:
        return self._tts.available

    # ── mute ───────────────────────────────────────────────

    def mute(self, seconds: float) -> float:
        """Silence everything for `seconds` (0 unmutes). Returns the end, epoch seconds."""
        with self._lock:
            self._muted_until = self._clock() + seconds if seconds > 0 else 0.0
            until = self._muted_until
        if seconds > 0:
            logger.info("muted for %ds", seconds)
        else:
            logger.info("unmuted")
        return until

    @property
    def muted_until(self) -> float:
        """End of the current mute, or 0 when not muted."""
        with self._lock:
            return self._muted_until if self._muted_until > self._clock() else 0.0

    # ── messages ───────────────────────────────────────────

    def submit(self, caught: CaughtMessage) -> Future:
        """The consumer's handler: handles the message on the worker thread."""
        return self._executor.submit(self.handle_message, caught.message)

    def handle_message(self, msg: Message) -> Announcement:
        entry = Announcement(system=msg.system, severity=msg.severity, title=msg.title, author=msg.author)
        rule = self._profile.match(msg)
        if rule is None:
            entry.outcome = "no_rule"
            return self._finish(entry)
        entry.rule = rule.name
        entry.volume = rule.volume
        entry.text = rule.render(msg)
        entry.clip = rule.sound
        if rule.silent:
            entry.outcome = "silent"
            return self._finish(entry)

        quiet = self._profile.quiet_hours
        if self.muted_until:
            entry.outcome = "muted"
        elif quiet is not None and quiet.silences(msg.severity):
            entry.outcome = "quiet_hours"
        elif self._is_repeat(entry):
            entry.outcome = "duplicate"
        # Quiet ones get their audio too, so they can be listened to later.
        entry.files = self._audio(entry.clip, entry.text)
        if entry.outcome == PLAYED and not entry.files:
            entry.outcome = "nothing"
        return self._finish(entry)

    def announce(
        self,
        text: str = "",
        clip: str = "",
        volume: int = 100,
        severity: str = "info",
        title: str = "",
        force: bool = False,
    ) -> Announcement:
        """POST /play: an announcement without a message. Respects mute unless `force`."""
        entry = Announcement(
            system="api",
            severity=severity,
            title=title or text[:80] or clip,
            text=" ".join(text.split()),
            clip=clip,
            volume=volume,
            origin="api",
        )
        if self.muted_until and not force:
            entry.outcome = "muted"
        entry.files = self._audio(clip, entry.text)
        if entry.outcome == PLAYED and not entry.files:
            entry.outcome = "nothing"
        return self._finish(entry)

    def replay(self, entry_id: int) -> Announcement | None:
        """Play a history entry again on the speaker — a person asked for it,
        so mute, quiet hours and repeats do not apply. None when it is gone."""
        original = self._history.get(entry_id)
        if original is None:
            return None
        files = [f for f in original.files if f.exists()]
        entry = Announcement(
            system=original.system,
            severity=original.severity,
            title=original.title,
            author=original.author,
            text=original.text,
            rule=original.rule,
            clip=original.clip,
            files=files,
            volume=original.volume,
            origin="replay",
            replay_of=original.id,
            outcome=PLAYED if files else "nothing",
        )
        return self._finish(entry, browser=False)

    # ── internals ──────────────────────────────────────────

    def _audio(self, clip: str, text: str) -> list[Path]:
        files: list[Path] = []
        if clip:
            path = self._sounds.find(clip)
            if path is not None:
                files.append(path)
            else:
                logger.warning("sound %s not found in %s", clip, self._sounds.describe())
        if text:
            speech = self._tts.synthesize(text)
            if speech is not None:
                files.append(speech)
        return files

    def _is_repeat(self, entry: Announcement) -> bool:
        """True when the same announcement played within DEDUP_WINDOW. A
        flapping check should be heard once, not every thirty seconds."""
        if self._dedup_window <= 0:
            return False
        key = (entry.system, entry.severity, entry.title, entry.text, entry.clip)
        now = self._clock()
        with self._lock:
            self._recent = {k: t for k, t in self._recent.items() if now - t < self._dedup_window}
            if key in self._recent:
                return True
            self._recent[key] = now
            return False

    def _finish(self, entry: Announcement, browser: bool = True) -> Announcement:
        if entry.audible:
            if self._browser and browser:
                entry.sinks.append("browser")
            if self._player is not None and self._player.play(
                Job(files=list(entry.files), volume=entry.volume, label=entry.title)
            ):
                entry.sinks.append("local")
        self._history.record(entry)
        logger.info(
            "announcement %s: %s / %s",
            entry.outcome,
            entry.system,
            entry.title,
            extra={"system": entry.system, "severity": entry.severity, "title": entry.title, "outcome": entry.outcome},
        )
        return entry

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._player is not None:
            self._player.close()
