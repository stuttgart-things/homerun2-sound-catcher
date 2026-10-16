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
"""

from __future__ import annotations

import logging
import queue
import shlex
import shutil
import subprocess  # nosec B404 - runs the configured player on files this process chose
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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


class LocalPlayer:
    def __init__(
        self,
        command: str,
        preroll: Path | None = None,
        run: Callable[..., object] = subprocess.run,
        max_queue: int = MAX_QUEUE,
    ) -> None:
        if not command:
            raise ValueError("SOUND_SINKS includes local, but no player was found: install mpv or set PLAYER_CMD")
        self._command = command
        self._preroll = preroll
        self._run = run
        self._queue: queue.Queue[Job | None] = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(target=self._loop, name="local-player", daemon=True)
        self._thread.start()
        logger.info("local sink: %s", shlex.split(command)[0])

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def play(self, job: Job) -> bool:
        """Queue a job. False when the queue is full and the job was dropped."""
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            logger.warning("local player busy, dropped %s", job.label or "announcement")
            return False

    def wait_idle(self) -> None:
        """Block until everything queued has played. For tests."""
        self._queue.join()

    def close(self) -> None:
        try:
            self._queue.put(None, timeout=1)
        except queue.Full:
            pass

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                files = ([self._preroll] if self._preroll else []) + job.files
                for file in files:
                    self._play_file(file, job.volume)
            finally:
                self._queue.task_done()

    def _play_file(self, file: Path, volume: int) -> None:
        argv = build_command(self._command, file, volume)
        try:
            self._run(argv, check=False, timeout=PLAY_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)  # nosec B603
        except Exception as exc:  # noqa: BLE001 - one broken file must not stop the speaker
            logger.error("player failed on %s", file.name, extra={"error": repr(exc)})
