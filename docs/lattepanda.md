# LattePanda setup

A native install on a LattePanda Mu or Iota (any amd64 box works the same),
with the office speaker on Bluetooth or a cable. These are the steps; they
are not yet tested on the real board.

## 1. OS and packages

Ubuntu Server 24.04 or Debian 12. Then:

```bash
sudo apt install -y git python3-venv mpv \
    pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth bluez
```

PipeWire runs per user session. Run the catcher as a normal user (here
`homerun`) and let that user's session start at boot:

```bash
sudo loginctl enable-linger homerun
```

## 2. Speaker

**Cable (preferred).** Plug the speaker into the board's audio out or a USB
sound card. `wpctl status` lists the sinks; `wpctl set-default <id>` picks
one.

**Bluetooth.** Put the speaker in pairing mode, then:

```bash
bluetoothctl
  power on
  scan on            # wait for the speaker, note its MAC
  pair  AA:BB:CC:DD:EE:FF
  trust AA:BB:CC:DD:EE:FF
  connect AA:BB:CC:DD:EE:FF
```

`trust` makes it reconnect by itself when it comes back. For speakers that
fall asleep, the timer in step 5 checks every minute.

Check that sound comes out before going on:

```bash
mpv /usr/share/sounds/alsa/Front_Center.wav   # or any file
```

## 3. Catcher and voice

```bash
cd ~
git clone https://github.com/stuttgart-things/homerun2-sound-catcher.git
cd homerun2-sound-catcher
python3 -m venv .venv
.venv/bin/pip install ".[tts,s3]"
.venv/bin/python -m piper.download_voices --data-dir voices en_US-lessac-medium de_DE-thorsten-medium
```

`TTS_VOICE` picks one by name. The default profile speaks English, so
`en_US-lessac-medium` fits it. For German, use `de_DE-thorsten-medium` with
German `say` templates: a voice reading another language sounds odd. More
voices: <https://huggingface.co/rhasspy/piper-voices> (`en_GB-alan-medium`,
`de_DE-thorsten-high`, which sounds better and is slower).

Try it without Redis:

```bash
SOUND_MODE=standalone SOUND_API_TOKEN=dev LOG_FORMAT=text \
  TTS_VOICE=en_US-lessac-medium TTS_VOICES_DIR=voices .venv/bin/sound-catcher &
curl -X POST localhost:8080/play -H 'Authorization: Bearer dev' \
  -H 'Content-Type: application/json' -d '{"sound":"chime.wav","say":"Hello office"}'
```

The page at `http://<lattepanda>:8080/` should list it.

## 4. Service

```bash
mkdir -p ~/.config/systemd/user
cp deploy/sound-catcher.service deploy/bt-reconnect.service deploy/bt-reconnect.timer ~/.config/systemd/user/
cp deploy/sound-catcher.env ~/.config/sound-catcher.env
$EDITOR ~/.config/sound-catcher.env      # Redis, token, voice path, SPEAKER_MAC
systemctl --user daemon-reload
systemctl --user enable --now sound-catcher
journalctl --user -u sound-catcher -f
```

## 5. Bluetooth reconnect (Bluetooth only)

```bash
systemctl --user enable --now bt-reconnect.timer
```

## 6. Sounds from S3 (optional)

Set `SOUNDS_S3_*` and the read-only keys in `~/.config/sound-catcher.env`
(see [s3-sounds.md](s3-sounds.md)), then `chmod 600` the file.

## 7. Tuning

- **First syllable cut off?** Raise `PREROLL` (`800ms`, `1s`).
- **Too loud or too quiet?** Use `volume` per rule in `profile.yaml`, and the
  master volume with `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.6`.
- **Different player?** `PLAYER_CMD`, e.g.
  `PLAYER_CMD="pw-play --volume={volume_frac} {file}"`.

## Updating

```bash
cd ~/homerun2-sound-catcher && git pull && .venv/bin/pip install ".[tts,s3]"
systemctl --user restart sound-catcher
```
