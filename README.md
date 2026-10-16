# homerun2-sound-catcher

Makes homerun2 messages audible. It consumes Redis Streams like the other
catchers and plays each message as a clip, a spoken announcement
([Piper](https://github.com/OHF-Voice/piper1-gpl), offline), or both, on the
office speaker and in every browser that has the player page open.

```
Redis stream ─► profile rule ─► mute / quiet hours / repeats ─► clip + speech ─► history ─┬─► local speaker
                                                                                           └─► player page (SSE)
```

The player page lists the announcements since your browser session started
(at most `HISTORY_SIZE`). Each one can be played again in the browser, or on
the speaker with the API token.

- [docs/concept.md](docs/concept.md): design, decisions, API
- [docs/lattepanda.md](docs/lattepanda.md): native install with speaker and systemd
- [docs/kubernetes.md](docs/kubernetes.md): browser-only in the cluster (KCL in `kcl/`)
- [docs/s3-sounds.md](docs/s3-sounds.md): sounds from an S3 bucket, and S3 authentication

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,tts,s3]"
task voice          # downloads en_US-lessac-medium into voices/
task run            # no Redis, browser only, token "dev" → http://localhost:8080/
task say -- "Pizza is here"
```

## Profile

```yaml
quietHours: { from: "18:00", to: "08:00", weekends: true, timezone: Europe/Berlin, allow: [critical] }
rules:
  backup:
    systems: [backup]
    severity: [error, critical]
    sound: alarm.wav
    say: "Backup auf {{ system }} fehlgeschlagen: {{ title }}"
    volume: 90
  quiet:
    severity: [info, debug]
    silent: true
```

See [profile.yaml](profile.yaml) for the bundled default. The clips in
`sounds/` are generated from sine tones by `hack/make_sounds.py`, so there are
no licence questions. Your own `.wav`, `.mp3`, `.ogg` or `.flac` files go in
`SOUNDS_DIR`, or in an S3 bucket, which takes precedence
([docs/s3-sounds.md](docs/s3-sounds.md)). `GET /api/sounds` lists what is
available.

## Environment

| Variable | Default | Description |
|---|---|---|
| `SOUND_MODE` | `full` | `full` consumes Redis; `standalone` has no Redis, only `POST /play` |
| `SOUND_SINKS` | `local,browser` | `local`: this machine's speaker. `browser`: open player pages |
| `REDIS_ADDR` / `REDIS_PORT` / `REDIS_PASSWORD` | `localhost` / `6379` / – | Redis |
| `REDIS_STREAMS` | – | Comma-separated streams; takes precedence over `REDIS_STREAM` |
| `REDIS_STREAM` | `messages` | Single stream (legacy) |
| `CONSUMER_GROUP` | `homerun2-sound-catcher` | Consumer group |
| `CONSUMER_NAME` | hostname | Consumer name |
| `CONSUMER_START_ID` | `$` | Where a new group starts |
| `MAX_MESSAGE_AGE` | `60s` | Older entries are acknowledged unplayed (`0`: off) |
| `REDIS_STARTUP_TIMEOUT` | `120s` | How long startup retries Redis |
| `PROFILE_PATH` | `profile.yaml` | Sound rules |
| `SOUNDS_DIR` | `<repo>/sounds` | Clips the rules and `/play` may name |
| `SOUNDS_S3_BUCKET` | – | Sync sounds from this bucket; see [docs/s3-sounds.md](docs/s3-sounds.md) for `SOUNDS_S3_*` and authentication |
| `TTS_VOICE` | – (image: `en_US-lessac-medium`) | A Piper voice: a name in `TTS_VOICES_DIR`, or a path to an `.onnx`. Empty: no speech, clips only |
| `TTS_VOICES_DIR` | `<repo>/voices` (image: `/app/voices`) | Where voice names are looked up. The image carries `en_US-lessac-medium` and `de_DE-thorsten-medium` |
| `TTS_CACHE_DIR` | `~/.cache/homerun2-sound-catcher` | Synthesized speech, pruned to 500 files |
| `PLAYER_CMD` | first of mpv, ffplay, pw-play | Local player; `{file}`, `{volume}` (0–100), `{volume_frac}` (0–1) |
| `PREROLL` | `500ms` | Silence before each local announcement, to wake a sleeping speaker (`0`: off) |
| `DEDUP_WINDOW` | `60s` | Same announcement within this window plays once (`0`: off) |
| `HISTORY_SIZE` | `20` | Announcements kept and shown on the player page |
| `SOUND_API_TOKEN` | – | Bearer token for `/play`, speaker replay and mute. Empty: those endpoints do not exist |
| `SOUND_API_MAX_TEXT` | `300` | Longest `say` accepted by `/play` |
| `SOUND_API_RATE_LIMIT` | `30` | Writes per minute, across all callers |
| `HEALTH_PORT` | `8080` | Web server port |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `info` | Logging |

## Development

```bash
task precommit      # ruff check, ruff format --check, pytest
task ci             # the same via Dagger, on Python 3.11 and the image's Python
```

The container image runs with `SOUND_SINKS=browser` and has Piper and
`en_US-lessac-medium` (default) and `de_DE-thorsten-medium` built in. It stays on Python 3.14 until onnxruntime,
which Piper needs, ships wheels for 3.15 (`renovate.json` holds it there).
