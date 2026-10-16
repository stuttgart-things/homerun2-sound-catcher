from __future__ import annotations

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


def test_player_plays_preroll_then_files_in_order():
    runs = []
    player = LocalPlayer("play {file} {volume}", preroll=Path("pre.wav"), run=lambda argv, **kw: runs.append(argv))
    player.play(Job(files=[Path("a.wav"), Path("b.wav")], volume=40))
    player.wait_idle()
    assert runs == [["play", "pre.wav", "40"], ["play", "a.wav", "40"], ["play", "b.wav", "40"]]
    player.close()


def test_player_survives_a_failing_command():
    runs = []

    def run(argv, **kw):
        runs.append(argv[1])
        if argv[1] == "bad.wav":
            raise OSError("no device")

    player = LocalPlayer("play {file}", run=run)
    player.play(Job(files=[Path("bad.wav")]))
    player.play(Job(files=[Path("good.wav")]))
    player.wait_idle()
    assert runs == ["bad.wav", "good.wav"]
    assert player.alive


def test_player_requires_a_command():
    with pytest.raises(ValueError, match="no player"):
        LocalPlayer("")
