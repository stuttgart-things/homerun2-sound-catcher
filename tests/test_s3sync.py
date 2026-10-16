from __future__ import annotations

import pytest

from sound_catcher.audio.library import SoundLibrary
from sound_catcher.audio.s3sync import S3SoundSync, normalize_prefix
from sound_catcher.config.settings import load_config


class FakeS3:
    """list_objects_v2 paginator and download_file over a dict of key -> bytes."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.etags: dict[str, str] = {}
        self.downloads: list[str] = []
        self.fail_list = False
        self.fail_keys: set[str] = set()

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        if self.fail_list:
            raise ConnectionError("endpoint down")
        contents = [
            {"Key": k, "Size": len(v), "ETag": f'"{self.etags.get(k, len(v))}"'}
            for k, v in self.objects.items()
            if k.startswith(Prefix)
        ]
        # Two pages, as a big bucket would come back.
        yield {"Contents": contents[:1]}
        yield {"Contents": contents[1:]}

    def download_file(self, bucket, key, path):
        if key in self.fail_keys:
            raise OSError("broken object")
        self.downloads.append(key)
        with open(path, "wb") as f:
            f.write(self.objects[key])


def test_prefix_normalization():
    assert normalize_prefix("") == ""
    assert normalize_prefix("/sounds/") == "sounds/"
    assert normalize_prefix("office/sounds") == "office/sounds/"


def test_sync_downloads_new_and_changed_and_removes_deleted(tmp_path):
    s3 = FakeS3({"sounds/tusch.mp3": b"aaa", "sounds/gong.wav": b"bb"})
    sync = S3SoundSync(s3, "bucket", tmp_path, prefix="sounds")
    status = sync.sync_once()
    assert status.files == 2 and not status.error
    assert (tmp_path / "tusch.mp3").read_bytes() == b"aaa"

    # Unchanged: nothing is downloaded again.
    s3.downloads.clear()
    sync.sync_once()
    assert s3.downloads == []

    # Changed (new ETag) and deleted.
    s3.objects["sounds/tusch.mp3"] = b"new"
    s3.etags["sounds/tusch.mp3"] = "v2"
    del s3.objects["sounds/gong.wav"]
    status = sync.sync_once()
    assert s3.downloads == ["sounds/tusch.mp3"]
    assert (tmp_path / "tusch.mp3").read_bytes() == b"new"
    assert not (tmp_path / "gong.wav").exists()
    assert status.files == 1


def test_sync_skips_what_it_must_not_store(tmp_path):
    s3 = FakeS3(
        {
            "readme.txt": b"x",
            "nested/deep.wav": b"x",
            "huge.wav": b"x" * 100,
            "ok.wav": b"x",
            "other.ogg": b"x",
        }
    )
    sync = S3SoundSync(s3, "bucket", tmp_path, max_bytes=50, max_files=1)
    status = sync.sync_once()
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith(".")) == ["ok.wav"]
    reasons = " ".join(status.skipped)
    assert "not audio" in reasons and "subfolder" in reasons and "larger" in reasons and "more than 1" in reasons


def test_unreachable_bucket_keeps_the_cache(tmp_path):
    s3 = FakeS3({"tusch.mp3": b"aaa"})
    sync = S3SoundSync(s3, "bucket", tmp_path)
    sync.sync_once()
    s3.fail_list = True
    status = sync.sync_once()
    assert "endpoint down" in status.error
    assert (tmp_path / "tusch.mp3").exists()
    assert status.to_json()["lastSync"]


def test_one_broken_object_does_not_stop_the_rest(tmp_path):
    s3 = FakeS3({"a.wav": b"a", "b.wav": b"b"})
    s3.fail_keys = {"a.wav"}
    status = S3SoundSync(s3, "bucket", tmp_path).sync_once()
    assert "a.wav" in status.error
    assert (tmp_path / "b.wav").exists() and not (tmp_path / "a.wav").exists()
    assert not list(tmp_path.glob("*.part"))


def test_library_prefers_s3_over_bundled(tmp_path):
    s3_dir, bundled = tmp_path / "s3", tmp_path / "bundled"
    s3_dir.mkdir()
    bundled.mkdir()
    (bundled / "chime.wav").write_bytes(b"bundled")
    (bundled / "ding.wav").write_bytes(b"bundled")
    (s3_dir / "chime.wav").write_bytes(b"s3")
    (s3_dir / ".manifest.json").write_text("{}")
    lib = SoundLibrary([("s3", s3_dir), ("bundled", bundled)])
    assert lib.find("chime.wav").read_bytes() == b"s3"
    assert lib.find("ding.wav").read_bytes() == b"bundled"
    assert lib.find("../bundled/ding.wav") is None
    assert lib.names() == {"chime.wav": "s3", "ding.wav": "bundled"}


def test_s3_config(monkeypatch):
    monkeypatch.setenv("SOUNDS_S3_BUCKET", "homerun-sounds")
    monkeypatch.setenv("SOUNDS_S3_ENDPOINT", "https://minio.example.com")
    monkeypatch.setenv("SOUNDS_S3_INTERVAL", "2m")
    monkeypatch.setenv("SOUNDS_S3_ANONYMOUS", "true")
    s3 = load_config().s3
    assert s3.enabled and s3.interval == 120 and s3.anonymous
    assert s3.region == "us-east-1"
    monkeypatch.setenv("SOUNDS_S3_INTERVAL", "1s")
    with pytest.raises(ValueError, match="SOUNDS_S3_INTERVAL"):
        load_config()
    monkeypatch.setenv("SOUNDS_S3_INTERVAL", "5m")
    monkeypatch.setenv("SOUNDS_S3_ANONYMOUS", "maybe")
    with pytest.raises(ValueError, match="SOUNDS_S3_ANONYMOUS"):
        load_config()
