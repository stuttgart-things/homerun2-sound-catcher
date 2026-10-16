"""The web app: player page, history, audio files, write API, health.

The player page is the browser sink. It shows the announcements since its
session started, at most ``HISTORY_SIZE`` of them, plays new ones as they
arrive when the browser sink is on, and can play any of them again — in the
browser for anyone, on the office speaker with the API token.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timezone
from importlib import resources
from typing import AsyncIterator, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from sound_catcher.config.settings import ApiConfig
from sound_catcher.dispatcher import Dispatcher
from sound_catcher.history import History
from sound_catcher.profile.engine import is_bare_name

MAX_MUTE_MINUTES = 12 * 60
MEDIA_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac"}

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    description="SOUND_API_TOKEN. Without it set, the write endpoints do not exist.",
)


class PlayRequest(BaseModel):
    """Body of ``POST /play``."""

    model_config = ConfigDict(extra="forbid")

    say: str = Field(default="", description="Text to speak. Capped by SOUND_API_MAX_TEXT.")
    sound: str = Field(default="", description="A clip in SOUNDS_DIR, by file name.", examples=["ding.wav"])
    volume: int = Field(default=100, ge=0, le=100)
    severity: Literal["debug", "info", "success", "warning", "error", "critical"] = "info"
    title: str = Field(default="", max_length=200, description="What the history shows. Defaults to the text.")
    force: bool = Field(default=False, description="Play even while muted.")

    @field_validator("sound")
    @classmethod
    def _bare_name(cls, value: str) -> str:
        if value and not is_bare_name(value):
            raise ValueError("must be a file name, not a path")
        return value


class MuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: float = Field(gt=0, le=MAX_MUTE_MINUTES)


class RateLimiter:
    """At most `limit` events in any `window` seconds. Thread-safe."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Take a slot. Returns 0 on success, else seconds until one frees up."""
        with self._lock:
            now = self._clock()
            while self._hits and now - self._hits[0] >= self._window:
                self._hits.popleft()
            if len(self._hits) >= self._limit:
                return self._window - (now - self._hits[0])
            self._hits.append(now)
            return 0.0


def _iso(epoch: float) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None


def create_app(
    dispatcher: Dispatcher,
    history: History,
    api: ApiConfig,
    history_size: int = 20,
    version: str = "dev",
    commit: str = "unknown",
    date: str = "unknown",
    health: Callable[[], dict] | None = None,
    stop: asyncio.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    app = FastAPI(title="homerun2-sound-catcher", version=version, docs_url=None, redoc_url=None)
    speaker = "local" in dispatcher.sinks

    def state() -> dict:
        return {
            "sinks": dispatcher.sinks,
            "speech": dispatcher.speech,
            "mutedUntil": _iso(dispatcher.muted_until),
            "historySize": history_size,
            "writable": api.writable,
            "speakerReplay": api.writable and speaker,
            "version": version,
        }

    # ── read side: no token ───────────────────────────────

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        page = resources.files("sound_catcher.web").joinpath("static/index.html").read_text()
        return HTMLResponse(page.replace("{{ version }}", version), headers={"Cache-Control": "no-cache"})

    @app.get("/api/state", tags=["player"], summary="Sinks, speech, mute and what the page may offer")
    async def get_state() -> dict:
        # serverTime: the page starts its session on the server's clock, so
        # a browser whose clock is off still sees its session's entries.
        return {**state(), "serverTime": time.time()}

    @app.get("/api/history", tags=["player"], summary="Announcements since a point in time, newest first")
    async def get_history(
        since: float = Query(default=0.0, description="Epoch seconds; the page passes its session start."),
        after: int = Query(default=0, ge=0, description="Only ids above this one."),
    ) -> list[dict]:
        return [e.to_json() for e in history.since(since, after)]

    @app.get("/api/events", tags=["player"], summary="SSE: new announcements and state changes")
    async def events(request: Request) -> EventSourceResponse:
        return EventSourceResponse(event_stream(history, request.is_disconnected, state, stop=stop), ping=15)

    @app.get("/audio/{entry_id}/{part}", tags=["player"], summary="One audio file of an announcement")
    async def audio(entry_id: int, part: int) -> FileResponse:
        # Addressed by announcement, never by path: only files the dispatcher
        # chose for an entry still in the history can be fetched.
        entry = history.get(entry_id)
        if entry is None or not 0 <= part < len(entry.files) or not entry.files[part].is_file():
            raise HTTPException(status_code=404, detail="no such audio")
        path = entry.files[part]
        return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))

    @app.get("/healthz", include_in_schema=False)
    @app.get("/health", include_in_schema=False)
    async def healthz() -> JSONResponse:
        body = {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "version": version}
        body.update({"commit": commit, "date": date})
        body.update(health() if health else {})
        return JSONResponse(body, status_code=503 if body["status"] != "ok" else 200)

    @app.get("/mute", tags=["control"], summary="Whether the catcher is muted, and until when")
    async def get_mute() -> dict:
        return {"mutedUntil": _iso(dispatcher.muted_until)}

    if api.writable:
        app.include_router(_write_router(dispatcher, api, speaker, clock))
    return app


def _write_router(dispatcher: Dispatcher, api: ApiConfig, speaker: bool, clock) -> APIRouter:
    """The endpoints that make noise or change state. Token and rate limit,
    shared across callers: the speaker is one object in one room."""
    router = APIRouter(
        responses={
            401: {"description": "Missing or wrong bearer token"},
            429: {"description": "Over SOUND_API_RATE_LIMIT writes per minute; see Retry-After"},
        }
    )
    limiter = RateLimiter(api.rate_limit, clock=clock)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
        given = credentials.credentials.encode() if credentials else b""
        if not secrets.compare_digest(given, api.token.encode()):
            raise HTTPException(status_code=401, detail="missing or wrong bearer token")
        retry_after = limiter.acquire()
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
            )

    @router.post(
        "/play", tags=["control"], summary="Say something and/or play a clip", dependencies=[Depends(authorize)]
    )
    async def play(req: PlayRequest) -> dict:
        if not req.say.strip() and not req.sound:
            raise HTTPException(status_code=422, detail="say or sound is required")
        if len(req.say) > api.max_text:
            raise HTTPException(status_code=422, detail=f"say is longer than {api.max_text} characters")
        entry = await asyncio.to_thread(
            dispatcher.announce,
            text=req.say,
            clip=req.sound,
            volume=req.volume,
            severity=req.severity,
            title=req.title,
            force=req.force,
        )
        return entry.to_json()

    if speaker:

        @router.post(
            "/api/history/{entry_id}/replay",
            tags=["player"],
            summary="Play a history entry again on the speaker",
            dependencies=[Depends(authorize)],
        )
        async def replay(entry_id: int) -> dict:
            entry = await asyncio.to_thread(dispatcher.replay, entry_id)
            if entry is None:
                raise HTTPException(status_code=404, detail=f"announcement {entry_id} is no longer in the history")
            return entry.to_json()

    @router.put("/mute", tags=["control"], summary="Mute for a number of minutes", dependencies=[Depends(authorize)])
    async def put_mute(req: MuteRequest) -> dict:
        return {"mutedUntil": _iso(dispatcher.mute(req.minutes * 60))}

    @router.delete("/mute", tags=["control"], summary="Unmute", dependencies=[Depends(authorize)])
    async def delete_mute() -> dict:
        dispatcher.mute(0)
        return {"mutedUntil": None}

    return router


async def event_stream(
    history: History,
    is_disconnected: Callable[[], Awaitable[bool]],
    state: Callable[[], dict],
    interval: float = 0.25,
    stop: asyncio.Event | None = None,
) -> AsyncIterator[dict]:
    """Yield SSE events until the client goes away or ``stop`` is set.

    Starts with a ``state`` event, then sends each new announcement once as an
    ``announcement`` event, and ``state`` again whenever it changes (mute).
    Ending on ``stop`` lets a shutdown complete open responses instead of
    cancelling them (led-catcher #105). Kept out of the route so tests can
    drive it directly.
    """
    last_id = history.last_id
    last_state = None
    while True:
        if (stop is not None and stop.is_set()) or await is_disconnected():
            break
        current = state()
        if current != last_state:
            last_state = current
            yield {"event": "state", "data": json.dumps(current)}
        # Oldest first, and last_id taken from what was sent, so an entry
        # recorded while this runs is picked up next round, not skipped.
        for entry in reversed(history.since(0, last_id)):
            yield {"event": "announcement", "data": json.dumps(entry.to_json())}
            last_id = max(last_id, entry.id)
        if stop is None:
            await asyncio.sleep(interval)
            continue
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except TimeoutError:
            pass
