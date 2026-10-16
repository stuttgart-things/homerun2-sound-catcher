"""Keeps a local copy of the sounds in an S3 bucket (MinIO, Ceph, AWS).

A thread lists the bucket under ``SOUNDS_S3_PREFIX`` at startup and every
``SOUNDS_S3_INTERVAL``, downloads what is new or changed (by ETag), and
removes local files whose object is gone. Rules keep naming sounds by file
name; the cache sits in front of the bundled clips (see library.py).

When the bucket cannot be reached the cache stays as it is, so the sounds
synced last keep playing. The error shows in /healthz, which stays healthy:
a missing upload is not a reason to restart the catcher.

Credentials come from the standard AWS chain (``AWS_ACCESS_KEY_ID`` /
``AWS_SECRET_ACCESS_KEY``, a profile, web identity), or none at all with
``SOUNDS_S3_ANONYMOUS`` for a public bucket. boto3 is the optional ``[s3]``
extra.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sound_catcher.audio.library import AUDIO_SUFFIXES
from sound_catcher.profile.engine import is_bare_name

logger = logging.getLogger(__name__)

MANIFEST = ".manifest.json"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 500


@dataclass
class SyncStatus:
    last_sync: float = 0.0  # epoch seconds of the last successful sync
    files: int = 0
    error: str = ""
    skipped: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "lastSync": self.last_sync or None,
            "files": self.files,
            "error": self.error or None,
            "skipped": self.skipped[:20],
        }


def normalize_prefix(prefix: str) -> str:
    prefix = prefix.strip().strip("/")
    return f"{prefix}/" if prefix else ""


class S3SoundSync:
    def __init__(
        self,
        client,
        bucket: str,
        dest: Path,
        prefix: str = "",
        interval: float = 300.0,
        max_bytes: int = MAX_FILE_BYTES,
        max_files: int = MAX_FILES,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = normalize_prefix(prefix)
        self._dest = dest
        self._interval = interval
        self._max_bytes = max_bytes
        self._max_files = max_files
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = SyncStatus()
        self._dest.mkdir(parents=True, exist_ok=True)

    @property
    def dest(self) -> Path:
        return self._dest

    def describe(self) -> str:
        return f"s3://{self._bucket}/{self._prefix}"

    # ── one pass ──────────────────────────────────────────

    def sync_once(self) -> SyncStatus:
        try:
            wanted, skipped = self._list()
        except Exception as exc:  # noqa: BLE001 - any S3 error leaves the cache as it was
            self.status.error = f"{type(exc).__name__}: {exc}"
            logger.warning("sound sync from %s failed: %s", self.describe(), self.status.error)
            return self.status

        manifest = self._read_manifest()
        errors = []
        for name, (key, etag) in wanted.items():
            if manifest.get(name) == etag and (self._dest / name).is_file():
                continue
            try:
                self._download(key, name)
                manifest[name] = etag
                logger.info("sound %s synced from %s", name, self.describe())
            except Exception as exc:  # noqa: BLE001 - one bad object must not stop the rest
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        for name in list(manifest):
            if name not in wanted:
                (self._dest / name).unlink(missing_ok=True)
                del manifest[name]
                logger.info("sound %s removed, no longer in %s", name, self.describe())
        self._write_manifest(manifest)

        self.status = SyncStatus(
            last_sync=time.time(),
            files=len([n for n in manifest if (self._dest / n).is_file()]),
            error="; ".join(errors),
            skipped=skipped,
        )
        return self.status

    def _list(self) -> tuple[dict[str, tuple[str, str]], list[str]]:
        """Objects to keep, by local name, and keys skipped with the reason."""
        wanted: dict[str, tuple[str, str]] = {}
        skipped: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key[len(self._prefix) :]
                if not name or name.endswith("/"):
                    continue
                if "/" in name or not is_bare_name(name):
                    skipped.append(f"{key} (subfolder)")
                elif PurePosixPath(name).suffix.lower() not in AUDIO_SUFFIXES:
                    skipped.append(f"{key} (not audio)")
                elif obj.get("Size", 0) > self._max_bytes:
                    skipped.append(f"{key} (larger than {self._max_bytes // (1024 * 1024)} MB)")
                elif len(wanted) >= self._max_files:
                    skipped.append(f"{key} (more than {self._max_files} files)")
                else:
                    wanted[name] = (key, str(obj.get("ETag", "")).strip('"'))
        return wanted, skipped

    def _download(self, key: str, name: str) -> None:
        tmp = self._dest / f".{name}.part"
        try:
            self._client.download_file(self._bucket, key, str(tmp))
            os.replace(tmp, self._dest / name)
        finally:
            tmp.unlink(missing_ok=True)

    def _read_manifest(self) -> dict[str, str]:
        try:
            return json.loads((self._dest / MANIFEST).read_text())
        except (OSError, ValueError):
            return {}

    def _write_manifest(self, manifest: dict[str, str]) -> None:
        tmp = self._dest / f"{MANIFEST}.part"
        tmp.write_text(json.dumps(manifest, sort_keys=True))
        os.replace(tmp, self._dest / MANIFEST)

    # ── background ───────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="s3-sound-sync", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sync_once()
            self._stop.wait(self._interval)

    def close(self) -> None:
        self._stop.set()


def create_s3_client(endpoint: str, region: str, anonymous: bool):
    """A boto3 S3 client. Path-style addressing, which MinIO and Ceph need and
    AWS still accepts; short timeouts, so a dead endpoint does not hang a sync."""
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
    except ImportError:
        raise ValueError("SOUNDS_S3_BUCKET is set but boto3 is not installed: pip install '.[s3]'") from None
    config = Config(
        s3={"addressing_style": "path"},
        connect_timeout=5,
        read_timeout=30,
        retries={"max_attempts": 2},
        signature_version=UNSIGNED if anonymous else None,
    )
    return boto3.client("s3", endpoint_url=endpoint or None, region_name=region or None, config=config)
