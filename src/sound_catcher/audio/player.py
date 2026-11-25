"""The local sink: plays announcements on this machine's audio output.

One thread works through a queue, so announcements play one after another
and never over each other. The queue is short (``MAX_QUEUE``): when a burst
arrives faster than it can be read out, the rest is dropped rather than
played for the next ten minutes. The history still lists it, and it can be
replayed from there.

Playback is an external command, ``PLAYER_CMD``, with ``{file}``,
``{volume}`` (0-100) and ``{volume_frac}`` (0.0-1.0) filled in. Without one,
the first of mpv, ffplay and pw-play on the PATH is used. Before each
announcement a short silence plays: Bluetooth speakers sleep, and the first
half second of whatever wakes them is lost.

An *ambient* job is programme, not news (the radio's time check): it is
taken only while the speaker is idle and nothing waits, so it never holds up
or crowds out a notification, and any other job cuts it off: its player is
stopped and the notification plays right away. An ambient job still waiting
when a notification arrives is skipped.
"""

from __future__ import annotations

import logging
import queue
import shlex
import shutil
import subprocess  # nosec B404 - runs the configured player on files this process chose
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_QUEUE = 5
PLAY_TIMEOUT_SECONDS = 120

PLAYERS = {
    "mpv": "mpv --no-video --really-quiet --volume={volume} {file}",
    "ffplay": "ffplay -nodisp -autoexit -loglevel quiet -volume {volume} {file}",
    "pw-play": "pw-play --volume={volume_frac} {file}",
}


def detect_player_cmd(which: Callable[[str], str | None] = shutil.which) -> str:
    """The command for the first known player on the PATH, or ""."""
    for name, cmd in PLAYERS.items():
        if which(name):
            return cmd
    return ""


def build_command(template: str, file: Path, volume: int) -> list[str]:
    """The argv for one file. Placeholders are filled per argument, after
    splitting, so a file name with spaces stays one argument."""
    values = {"file": str(file), "volume": str(volume), "volume_frac": f"{volume / 100:.2f}"}
    return [arg.format(**values) for arg in shlex.split(template)]


@dataclass
class Job:
    files: list[Path]
    volume: int = 100
    label: str = ""
    ambient: bool = False
    # Called (on the player thread) when another job cut this one off.
    on_cut: Callable[[], None] | None = None
    cut: bool = field(default=False, init=False)
    news_seen: int = field(default=0, init=False, repr=False)  # news jobs queued before this one


class LocalPlayer:
    def __init__(
        self,
        command: str,
        preroll: Path | None = None,
        spawn: Callable[..., Any] = subprocess.Popen,
        max_queue: int = MAX_QUEUE,
    ) -> None:
        if not command:
            raise ValueError("SOUND_SINKS includes local, but no player was found: install mpv or set PLAYER_CMD")
        self._command = command
        self._preroll = preroll
        self._spawn = spawn
        self._queue: queue.Queue[Job | None] = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._current: Job | None = None  # the job playing now
        self._proc: Any = None  # its player process
        self._news = 0  # non-ambient jobs queued so far
        self._thread = threading.Thread(target=self._loop, name="local-player", daemon=True)
        self._thread.start()
        logger.info("local sink: %s", shlex.split(command)[0])

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def play(self, job: Job) -> bool:
        """Queue a job. False when it was dropped: the queue is full, or the
        job is ambient and the speaker is busy."""
        with self._lock:
            if job.ambient:
                if self._current is not None or not self._queue.empty():
                    logger.info("local player busy, dropped ambient %s", job.label or "announcement")
                    return False
                job.news_seen = self._news
            else:
                self._news += 1
                current = self._current
                if current is not None and current.ambient and not current.cut:
                    self._cut(current)
            try:
                self._queue.put_nowait(job)
                return True
            except queue.Full:
                if not job.ambient:
                    self._news -= 1
                logger.warning("local player busy, dropped %s", job.label or "announcement")
                return False

    def wait_idle(self) -> None:
        """Block until everything queued has played. For tests."""
        self._queue.join()

    def close(self) -> None:
        with self._lock:
            if self._current is not None:
                self._cut(self._current, notify=False)
        try:
            self._queue.put(None, timeout=1)
        except queue.Full:
            pass

    def _cut(self, job: Job, notify: bool = True) -> None:
        """Stop `job`, which is playing. Called with the lock held."""
        job.cut = True
        if not notify:
            job.on_cut = None
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception as exc:  # noqa: BLE001 - it may have ended on its own
                logger.debug("terminate failed", extra={"error": repr(exc)})

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                with self._lock:
                    if job.ambient and self._news > job.news_seen:
                        logger.info("skipped ambient %s: a notification came after it", job.label or "announcement")
                        job.cut = True
                    else:
                        self._current = job
                if job.cut:
                    if job.on_cut is not None:
                        job.on_cut()
                    continue
                files = ([self._preroll] if self._preroll else []) + job.files
                for file in files:
                    if job.cut:
                        break
                    self._play_file(job, file)
                with self._lock:
                    self._current = None
                if job.cut:
                    logger.info("cut off %s", job.label or "announcement")
                    if job.on_cut is not None:
                        job.on_cut()
            finally:
                self._queue.task_done()

    def _play_file(self, job: Job, file: Path) -> None:
        argv = build_command(self._command, file, job.volume)
        try:
            with self._lock:
                if job.cut:
                    return
                proc = self._spawn(argv, stdin=subprocess.DEVNULL)  # nosec B603
                self._proc = proc
            try:
                proc.wait(timeout=PLAY_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                logger.error("player timed out on %s", file.name)
        except Exception as exc:  # noqa: BLE001 - one broken file must not stop the speaker
            logger.error("player failed on %s", file.name, extra={"error": repr(exc)})
        finally:
            with self._lock:
                self._proc = None
