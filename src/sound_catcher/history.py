"""The announcement history: what was played (or kept quiet), newest first.

A ring buffer of ``HISTORY_SIZE`` entries. The player page asks for the
entries since its session started and follows new ones over SSE; it never
sees anything older than its session, nor more than the buffer holds.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

# Why an announcement made a sound or did not.
PLAYED = "played"
OUTCOMES = {
    PLAYED: "gespielt",
    "silent": "stumm (Regel)",
    "no_rule": "keine Regel",
    "quiet_hours": "Ruhezeit",
    "muted": "stummgeschaltet",
    "duplicate": "Wiederholung",
    "nothing": "nichts abzuspielen",
}


@dataclass
class Announcement:
    system: str = ""
    severity: str = "info"
    title: str = ""
    author: str = ""
    text: str = ""  # what is (or would be) spoken
    rule: str = ""
    clip: str = ""
    files: list[Path] = field(default_factory=list, repr=False)  # clip, then speech
    volume: int = 100
    outcome: str = PLAYED
    origin: str = "stream"  # stream, api, replay
    replay_of: int = 0
    sinks: list[str] = field(default_factory=list)  # where it was sent
    at: float = field(default_factory=time.time)
    id: int = 0  # assigned by History.record

    @property
    def audible(self) -> bool:
        return self.outcome == PLAYED and bool(self.files)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "at": self.at,
            "system": self.system,
            "severity": self.severity,
            "title": self.title,
            "author": self.author,
            "text": self.text,
            "rule": self.rule,
            "clip": self.clip,
            "volume": self.volume,
            "outcome": self.outcome,
            "outcomeText": OUTCOMES.get(self.outcome, self.outcome),
            "origin": self.origin,
            "replayOf": self.replay_of,
            "sinks": self.sinks,
            # Paths the browser may fetch, in play order. Present for a quiet
            # announcement too, so it can still be listened to on demand.
            "audio": [f"/audio/{self.id}/{n}" for n in range(len(self.files))],
            # Whether open player pages play it on arrival.
            "autoplay": "browser" in self.sinks,
        }


class History:
    """Thread-safe ring buffer. Ids only grow, so SSE follows `last_id`."""

    def __init__(self, size: int = 20) -> None:
        self._entries: deque[Announcement] = deque(maxlen=size)
        self._lock = threading.Lock()
        self._next_id = 0

    def record(self, entry: Announcement) -> Announcement:
        with self._lock:
            self._next_id += 1
            entry.id = self._next_id
            self._entries.appendleft(entry)
        return entry

    def get(self, entry_id: int) -> Announcement | None:
        with self._lock:
            return next((e for e in self._entries if e.id == entry_id), None)

    def since(self, at: float = 0.0, after_id: int = 0) -> list[Announcement]:
        """Entries at or after `at` and with an id above `after_id`, newest first."""
        with self._lock:
            return [e for e in self._entries if e.at >= at and e.id > after_id]

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._next_id
