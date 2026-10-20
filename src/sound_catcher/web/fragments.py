"""Micro-frontend fragments for homerun2-portal.

The portal reads ``{base_path}/ui/manifest.json`` to learn which fragments a
service offers, then loads each ``{base_path}/ui/fragment/{id}`` with htmx into
its own page. A fragment is plain HTML without ``<head>`` or ``<script>``: it
is styled only by the portal's ``hr-*`` classes (``static/homerun2.css`` in
homerun2-portal), and every URL in it carries the base path, because it is
served on the portal's origin.

The announcements fragment reads the history ring buffer (``HISTORY_SIZE``),
the same one the player page reads: stream messages, ``POST /play`` and
replays all land there.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Callable

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from sound_catcher.history import OUTCOMES, Announcement, History

ANNOUNCEMENTS_LIMIT = 8
ANNOUNCEMENTS_REFRESH_SECONDS = 5

# The portal's severity classes: hr-sev-error, hr-sev-warning, ...
_SEVERITIES = {"error", "warning", "success", "info", "debug"}


def create_fragments_router(
    history: History, muted_until: Callable[[], float], version: str = "dev", base_path: str = ""
) -> APIRouter:
    """``/ui/manifest.json`` and the fragments it lists."""
    router = APIRouter(include_in_schema=False)

    @router.get("/ui/manifest.json")
    async def manifest() -> dict:
        return {
            "name": "homerun2-sound-catcher",
            "title": "Sound Catcher",
            "version": version,
            "basePath": base_path,
            "fragments": [
                {
                    "id": "announcements",
                    "title": "Latest announcements",
                    "url": f"{base_path}/ui/fragment/announcements",
                    "refreshSeconds": ANNOUNCEMENTS_REFRESH_SECONDS,
                }
            ],
        }

    @router.get("/ui/fragment/announcements", response_class=HTMLResponse)
    async def announcements_fragment() -> HTMLResponse:
        page = render_announcements_fragment(history.since()[:ANNOUNCEMENTS_LIMIT], muted_until(), base_path)
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    return router


def render_announcements_fragment(entries: list[Announcement], muted_until: float = 0.0, base_path: str = "") -> str:
    """The latest announcements as an ``hr-list``, newest first, with the
    mute state above them.

    Every field is escaped: titles and systems come from messages and from
    POST /play, and the portal shows the fragment to every viewer.
    """
    mute = ""
    if muted_until:
        mute = f'<p class="hr-muted">Muted until {_clock_time(muted_until, "%H:%M")}</p>'
    if not entries:
        rows = '<li class="hr-item hr-muted">No announcements yet.</li>'
    else:
        rows = "".join(_render_item(e) for e in entries)
    return (
        '<div class="hr-frag">'
        f"{mute}"
        f'<ul class="hr-list">{rows}</ul>'
        f'<a class="hr-frag-more" href="{html.escape(base_path)}/">Open player →</a>'
        "</div>"
    )


def _render_item(e: Announcement) -> str:
    severity = e.severity.lower()
    if severity == "critical":
        severity = "error"
    if severity not in _SEVERITIES:
        severity = "info"
    outcome = "replayed" if e.origin == "replay" else OUTCOMES.get(e.outcome, e.outcome)
    return (
        '<li class="hr-item">'
        f'<span class="hr-time">{_clock_time(e.at)}</span>'
        f'<span class="hr-sev hr-sev-{severity}">{html.escape(e.severity.upper() or "INFO")}</span>'
        f'<span class="hr-system">{html.escape(e.system)}</span>'
        f'<span class="hr-title">{html.escape(e.title)}</span>'
        # Non-breaking spaces keep "quiet hours" on one line: the fragment has
        # only the portal's classes, and the title's ellipsis gives way instead.
        f'<span class="hr-muted">{html.escape(outcome).replace(" ", "&nbsp;")}</span>'
        "</li>"
    )


def _clock_time(epoch: float, fmt: str = "%H:%M:%S") -> str:
    """Local time of the catcher — the speaker's room, where it was heard."""
    return datetime.fromtimestamp(epoch).strftime(fmt)
