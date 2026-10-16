# CLAUDE.md

## Project

homerun2-sound-catcher — Python microservice that consumes messages from Redis Streams and plays them as clips and spoken announcements (Piper TTS) on a local speaker and in the browser (player page with session history and replay). Runs natively on a LattePanda next to the office speaker; `SOUND_MODE=standalone` runs without Redis.

## Tech Stack

- **Language**: Python 3.11+
- **Consumer**: Redis Streams via `redis-py` async (consumer groups), same flow as led-catcher
- **TTS**: `piper-tts` (optional extra `[tts]`), cached on disk
- **Playback**: external player (mpv / ffplay / pw-play) on one queue thread
- **Web**: FastAPI + SSE (sse-starlette), a single static page with vanilla JS
- **CI**: Dagger module `stuttgart-things/dagger/python`, GitHub Actions, Taskfile
- **Infra**: semantic-release, renovate

## Git Workflow

Branch-per-issue with PR and merge, as in the sibling repos:

- `fix/<issue>-<desc>`, `feat/<issue>-<desc>`, `test/<issue>-<desc>`, `chore/<issue>-<desc>`
- Conventional commits (`fix:`, `feat:`, `test:`, `chore:`, `docs:`), `Closes #<issue>`
- Run `task precommit` before every commit (`task setup-precommit` installs the hook)

## Code Conventions

- `src/` layout (`src/sound_catcher/`)
- Config via environment variables, loaded once at startup (`config/settings.py`); a typo fails startup
- Unit tests must not need Redis, a speaker or a Piper voice: use the fakes in `tests/conftest.py`
- Logging: Python `logging` with JSON formatter (matching Go slog output)
- Write endpoints fail closed: no `SOUND_API_TOKEN`, no endpoint

## Key Paths

- `src/sound_catcher/__main__.py` — entrypoint, composition, shutdown
- `src/sound_catcher/consumer/redis_consumer.py` — RedisConsumer (JSON.GET payload, MAX_MESSAGE_AGE)
- `src/sound_catcher/dispatcher.py` — rule → mute / quiet hours / dedup → audio → history → sinks; replay
- `src/sound_catcher/history.py` — Announcement + ring buffer, JSON for the page
- `src/sound_catcher/profile/engine.py` — YAML rules, quiet hours, sandboxed Jinja `say`
- `src/sound_catcher/audio/tts.py` — Piper synthesizer with cache, silence pre-roll
- `src/sound_catcher/audio/player.py` — local sink: queue, player command
- `src/sound_catcher/web/app.py` — routes, write API, SSE generator
- `src/sound_catcher/web/static/index.html` — the player page
- `docs/concept.md` — design and API; `docs/lattepanda.md` — native install
- `deploy/` — systemd user units, env template, Bluetooth reconnect timer

## Testing

```bash
pip install -e ".[dev,tts]"
task precommit
task run                     # standalone, browser only, token "dev"
task say -- "Hallo Büro"
```

## Reference Projects

- `homerun2-led-catcher` — sibling Python catcher (same consumer, /display API pattern)
- `homerun2-light-catcher` — sibling Go catcher (profiles, MAX_MESSAGE_AGE)
