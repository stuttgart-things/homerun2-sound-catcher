# Concept

homerun2-sound-catcher makes homerun2 messages audible. It reads the same
Redis streams as the other catchers and turns a message into a short clip,
a spoken announcement, or both. The announcement plays on the office speaker
and in any browser that has the player page open.

## Where it sits

```
                    ┌─► Pi 3B        led-catcher    (group homerun2-led-catcher)    64x64 matrix
Redis stream ───────┼─► WLED         light-catcher  (group homerun2-light-catcher)  lights
                    └─► LattePanda   sound-catcher  (group homerun2-sound-catcher)  speaker + browser
```

Each catcher has its own consumer group, so each one sees every message and
decides for itself, through its own profile, what to do with it. A `critical`
can light up the matrix and sound the alarm, while `info` shows on the matrix
and stays silent. The catchers are not coupled.

### Why not on the Pi 3B next to the matrix

- rpi-rgb-led-matrix keeps one core busy refreshing the panel. Audio decoding,
  and speech synthesis even more, disturbs that timing, and the panel flickers.
- Piper runs on a Pi 3, but slowly: seconds per sentence, on the cores the
  matrix shares.
- The Pi 3B has one chip for Wi-Fi and Bluetooth, 2.4 GHz only. A2DP streaming
  alongside Wi-Fi drops out.
- The matrix needs the onboard sound turned off, because both use the PWM
  hardware.

The LattePanda (Mu or Iota, Intel N100/N150 class) has none of these problems.
It runs Piper in well under a second per sentence and is plain amd64 Linux.

## The pipeline

```
message ─► profile rule ─► mute? quiet hours? repeat? ─► clip + speech (Piper, cached) ─► history ─┬─► local sink (speaker)
                                                                                                    └─► browser sink (SSE)
```

1. **Rule.** `profile.yaml` maps system, severity and tags to a clip
   (`sound`), a spoken Jinja template (`say`), a volume, or `silent`. Rules
   are tried in order and the first match wins, as in the other catchers.
2. **Should it make a sound now?**
   - *Mute*: `PUT /mute`, for meetings. It ends by itself.
   - *Quiet hours*: from the profile. Only the `allow` severities sound
     outside office hours.
   - *Repeats*: the same announcement within `DEDUP_WINDOW` (default 60s)
     plays once. A flapping check should be heard once, not every 30 seconds.
   - *Age*: entries older than `MAX_MESSAGE_AGE` are acknowledged unplayed.
     A backlog read out loud after a restart is the fastest way to get the
     speaker unplugged.

   An announcement held back by any of these is still recorded, with the
   reason, and can still be listened to on demand.
3. **Audio.** The clip comes from `sounds/`. The speech comes from Piper and
   is cached under the hash of voice and text, so repeats cost nothing.
   Without Piper or a voice, rules play their clips only.
4. **History.** A ring buffer of the last `HISTORY_SIZE` announcements. It is
   what the browser sink follows.
5. **Sinks** (`SOUND_SINKS`):
   - `local` runs a player (mpv, ffplay or pw-play) on one queue, so
     announcements never overlap. Before each one, half a second of silence
     plays (`PREROLL`), because a sleeping Bluetooth speaker eats the start
     of whatever wakes it. When a burst outruns the speaker, the queue
     (5 deep) drops the rest instead of reading for ten minutes.
   - `browser` pushes each announcement over SSE to every open player page,
     which plays it.

## The player page (browser sink)

- Shows the announcements **since this browser session started**, newest
  first, **at most `HISTORY_SIZE`**. Nothing from before the session, so
  opening the page does not replay the morning. The session lives in
  `sessionStorage`: it survives a reload, and a new tab is a new session.
  "Neue Sitzung" starts over.
- **"Im Browser anhören"** replays any entry in that browser only, quiet ones
  too.
- **"Auf Lautsprecher"** replays an entry on the office speaker. This needs
  the API token, entered once per session. A person asked for it, so mute
  and quiet hours do not apply, and open pages do not play it again.
- New announcements play automatically while "Neue automatisch abspielen" is
  on. Browsers only allow audio after a click, so the page asks for one
  ("Ton aktivieren"). A kiosk Chromium can skip that with
  `--autoplay-policy=no-user-gesture-required`.
- Mute for 15 min or 1 h, and unmute, with the token.

Limits worth knowing:

- Phones pause background tabs and locked screens. A phone in a pocket is not
  a reliable sink; a tablet with its screen kept on is.
- After a network drop the page reconnects and reloads its list, but does not
  play what it missed. By then it is not news.
- Without a token, anyone who reaches the page can listen. Keep it on the LAN
  or put it behind auth.

## API

| Endpoint | Token | What |
|---|---|---|
| `GET /` | – | Player page |
| `GET /api/state` | – | Sinks, speech, mute, history size, what the page may offer |
| `GET /api/history?since=&after=` | – | Announcements, newest first |
| `GET /api/events` | – | SSE: `state`, `announcement` |
| `GET /audio/{id}/{n}` | – | Audio of an announcement, by id (never by path) |
| `POST /play` | ✓ | `{"say": "...", "sound": "chime.wav", "volume": 80, "force": false}` |
| `POST /api/history/{id}/replay` | ✓ | Replay on the speaker (local sink only) |
| `GET /mute`, `PUT /mute`, `DELETE /mute` | `PUT`/`DELETE` ✓ | Mute for `{"minutes": n}` |
| `GET /healthz` | – | 503 when the consumer failed or the player thread died |

As with led-catcher's `/display`: without `SOUND_API_TOKEN` the write
endpoints are not registered at all, and writes are rate-limited across all
callers (`SOUND_API_RATE_LIMIT` per minute). The speaker is one object in one
room.

## Hardware notes

- **Bluetooth.** Check that the board has it. The Mu gets it from its carrier
  board, usually via an M.2 E-key Wi-Fi/BT card. If there is none, an Intel
  AX210 in M.2 E-key or a USB dongle works. `deploy/bt-reconnect.timer`
  reconnects a speaker that went to sleep.
- **A cable beats Bluetooth.** If the speaker has an AUX input, use the
  board's jack or a cheap USB sound card. No pairing, no sleep, no dropouts.
- Run it **natively under systemd** (`docs/lattepanda.md`), not in
  Kubernetes. Audio devices in containers are possible but fiddly. The
  container image is for browser-only setups.

## Not built yet / ideas

- KCL manifests for a browser-only deployment in the cluster.
- Per-rule voices (a second Piper voice for `critical`).
- A volume ramp for alarms that repeat.
- `led-catcher-publish` already writes test messages to the stream. A shared
  publisher in homerun-library would serve all catchers.
