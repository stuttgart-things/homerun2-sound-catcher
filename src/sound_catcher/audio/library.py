"""Where a sound named in a rule or in POST /play is looked up.

An ordered list of directories, first match wins: the S3 cache (when a bucket
is configured) before the bundled `SOUNDS_DIR`, so a bucket can replace a
bundled clip by uploading one with the same name.
"""

from __future__ import annotations

from pathlib import Path

from sound_catcher.profile.engine import is_bare_name

AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac"}


class SoundLibrary:
    def __init__(self, dirs: list[tuple[str, Path]]) -> None:
        """`dirs`: (source label, directory), in lookup order."""
        self._dirs = dirs

    @classmethod
    def of(cls, sounds: SoundLibrary | Path) -> SoundLibrary:
        return sounds if isinstance(sounds, SoundLibrary) else cls([("bundled", sounds)])

    def find(self, name: str) -> Path | None:
        if not is_bare_name(name):
            return None
        for _, directory in self._dirs:
            path = directory / name
            if path.is_file():
                return path
        return None

    def names(self) -> dict[str, str]:
        """Every available sound and where it comes from, as GET /api/sounds lists them."""
        found: dict[str, str] = {}
        for source, directory in self._dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES and path.name not in found:
                    found[path.name] = source
        return found

    def describe(self) -> str:
        return ", ".join(str(d) for _, d in self._dirs)
