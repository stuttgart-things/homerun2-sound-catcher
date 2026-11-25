from __future__ import annotations

import threading
import wave
from pathlib import Path

import pytest
from conftest import fake_render

from sound_catcher.audio.player import Job, LocalPlayer, build_command, detect_player_cmd
from sound_catcher.audio.tts import CachedSynthesizer, NoSpeech, create_synthesizer, write_silence


def test_synthesis_is_cached_by_voice_and_text(tmp_path):
    calls = []

    def render(text, wav_file):
        calls.append(text)
        fake_render(text, wav_file)

    synth = CachedSynthesizer("v1", tmp_path, render)
    a = synth.synthesize("Hallo Büro")
    b = synth.synthesize("Hallo Büro")
    assert a == b and a.exists()
    assert calls == ["Hallo Büro"]
    other_voice = CachedSynthesizer("v2", tmp_path, render)
    assert other_voice.path_for("Hallo Büro") != a
    assert synth.synthesize("   ") is None


def test_failed_synthesis_leaves_nothing_behind(tmp_path):
    def broken(text, wav_file):
        raise RuntimeError("boom")

    synth = CachedSynthesizer("v", tmp_path, broken)
    assert synth.synthesize("x") is None
    assert list(tmp_path.iterdir()) == []


def test_cache_is_pruned_oldest_first(tmp_path):
    synth = CachedSynthesizer("v", tmp_path, fake_render, max_files=2)
    first = synth.synthesize("eins")
    synth.synthesize("zwei")
    synth.synthesize("drei")
    assert not first.exists()
    assert len(list(tmp_path.glob("tts-*.wav"))) == 2


def test_no_voice_means_no_speech(tmp_path):
    assert isinstance(create_synthesizer("", tmp_path), NoSpeech)
    with pytest.raises(ValueError, match="no such voice"):
        create_synthesizer(str(tmp_path / "missing.onnx"), tmp_path)


def test_voices_by_name_or_path(tmp_path):
    from sound_catcher.audio.tts import resolve_voice

    assert resolve_voice("en_US-lessac-medium", tmp_path) == tmp_path / "en_US-lessac-medium.onnx"
    assert resolve_voice("/opt/v/x.onnx", tmp_path) == Path("/opt/v/x.onnx")
    assert resolve_voice("voices/x.onnx", tmp_path) == Path("voices/x.onnx")
    (tmp_path / "de_DE-thorsten-medium.onnx").write_bytes(b"")
    with pytest.raises(ValueError, match="voices there: de_DE-thorsten-medium"):
        create_synthesizer("en_US-lessac-medium", tmp_path, voices_dir=tmp_path)


def test_silence_is_a_valid_wav(tmp_path):
    path = write_silence(tmp_path / "s.wav", 0.5, rate=8000)
    with wave.open(str(path)) as w:
        assert w.getnframes() == 4000


def test_build_command_keeps_paths_with_spaces_whole():
    argv = build_command("mpv --volume={volume} {file}", Path("/a b/c.wav"), 70)
    assert argv == ["mpv", "--volume=70", "/a b/c.wav"]
    assert build_command("pw-play --volume={volume_frac} {file}", Path("x.wav"), 50)[1] == "--volume=0.50"


def test_detect_player_prefers_mpv():
    assert detect_player_cmd(lambda n: "/usr/bin/" + n).startswith("mpv")
    assert detect_player_cmd(lambda n: "/usr/bin/pw-play" if n == "pw-play" else None).startswith("pw-play")
    assert detect_player_cmd(lambda n: None) == ""


class FakeProc:
    """A player process. `block` keeps it running until terminated."""

    def __init__(self, argv, block: threading.Event | None = None) -> None:
        self.argv = argv
        self._done = threading.Event()
        self.terminated = False
        if block is None:
            self._done.set()

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return 0

    def terminate(self):
        self.terminated = True
        self._done.set()

    def kill(self):
        self._done.set()


class Spawner:
    """Records every argv; files named in `blocking` keep playing until cut off."""

    def __init__(self, blocking: tuple[str, ...] = (), fail: tuple[str, ...] = ()) -> None:
        self.argvs: list[list[str]] = []
        self.procs: list[FakeProc] = []
        self.blocking = blocking
        self.fail = fail
        self.started = threading.Event()

    def __call__(self, argv, **kw):
        self.argvs.append(argv)
        if argv[1] in self.fail:
            raise OSError("no device")
        proc = FakeProc(argv, threading.Event() if argv[1] in self.blocking else None)
        self.procs.append(proc)
        if argv[1] in self.blocking:
            self.started.set()
        return proc

    @property
    def files(self) -> list[str]:
        return [a[1] for a in self.argvs]


def test_player_plays_preroll_then_files_in_order():
    spawn = Spawner()
    player = LocalPlayer("play {file} {volume}", preroll=Path("pre.wav"), spawn=spawn)
    player.play(Job(files=[Path("a.wav"), Path("b.wav")], volume=40))
    player.wait_idle()
    assert spawn.argvs == [["play", "pre.wav", "40"], ["play", "a.wav", "40"], ["play", "b.wav", "40"]]
    player.close()


def test_player_survives_a_failing_command():
    spawn = Spawner(fail=("bad.wav",))
    player = LocalPlayer("play {file}", spawn=spawn)
    player.play(Job(files=[Path("bad.wav")]))
    player.play(Job(files=[Path("good.wav")]))
    player.wait_idle()
    assert spawn.files == ["bad.wav", "good.wav"]
    assert player.alive


def test_ambient_is_dropped_while_the_speaker_is_busy():
    spawn = Spawner(blocking=("news.wav",))
    player = LocalPlayer("play {file}", spawn=spawn)
    assert player.play(Job(files=[Path("news.wav")]))
    assert spawn.started.wait(2)
    assert not player.play(Job(files=[Path("radio.wav")], ambient=True))
    spawn.procs[0].terminate()  # the notification ends
    player.wait_idle()
    assert spawn.files == ["news.wav"]


def test_any_notification_cuts_off_ambient_and_plays_at_once():
    spawn = Spawner(blocking=("radio1.wav",))
    cut = []
    player = LocalPlayer("play {file}", spawn=spawn)
    assert player.play(Job(files=[Path("radio1.wav"), Path("radio2.wav")], ambient=True, on_cut=lambda: cut.append(1)))
    assert spawn.started.wait(2)
    assert player.play(Job(files=[Path("news.wav")]))
    player.wait_idle()
    assert spawn.procs[0].terminated
    assert spawn.files == ["radio1.wav", "news.wav"]  # the rest of the programme is not played
    assert cut == [1]


def test_ambient_still_waiting_is_skipped_for_a_notification():
    # The race play() cannot rule out: an ambient job queued on an idle
    # speaker that the player thread has not started yet when news arrives.
    spawn = Spawner(blocking=("hold.wav",))
    skipped = []
    player = LocalPlayer("play {file}", spawn=spawn)
    assert player.play(Job(files=[Path("hold.wav")]))
    assert spawn.started.wait(2)
    ambient = Job(files=[Path("radio.wav")], ambient=True, on_cut=lambda: skipped.append(1))
    ambient.news_seen = player._news  # as play() records it on an idle speaker
    player._queue.put_nowait(ambient)
    assert player.play(Job(files=[Path("news.wav")]))
    spawn.procs[0].terminate()
    player.wait_idle()
    assert spawn.files == ["hold.wav", "news.wav"]
    assert skipped == [1]


def test_a_notification_never_cuts_off_a_notification():
    spawn = Spawner(blocking=("first.wav",))
    player = LocalPlayer("play {file}", spawn=spawn)
    assert player.play(Job(files=[Path("first.wav")]))
    assert spawn.started.wait(2)
    assert player.play(Job(files=[Path("second.wav")]))
    assert not spawn.procs[0].terminated
    spawn.procs[0].terminate()
    player.wait_idle()
    assert spawn.files == ["first.wav", "second.wav"]


def test_player_requires_a_command():
    with pytest.raises(ValueError, match="no player"):
        LocalPlayer("")
