"""Redis Streams consumer with consumer groups — the homerun2 catcher pattern.

The same flow as led-catcher's consumer, without its runtime stream switching:
XREADGROUP, take the entry's ``messageID``, resolve the payload with
``JSON.GET``, hand it to the handlers, XACK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Callable

import redis.asyncio as aioredis

from sound_catcher.config import Config
from sound_catcher.models import CaughtMessage, Message

logger = logging.getLogger(__name__)

MessageHandler = Callable[[CaughtMessage], None]

BLOCK_MS = 5000
SOCKET_TIMEOUT_SECONDS = BLOCK_MS / 1000 + 5
"""Has to outlast a blocking XREADGROUP; redis-py's default of 5s equals BLOCK_MS
and turns every empty read into a timeout (led-catcher #56)."""

STARTUP_ATTEMPT_TIMEOUT_SECONDS = 5.0
STARTUP_BACKOFF_INITIAL_SECONDS = 1.0
STARTUP_BACKOFF_MAX_SECONDS = 16.0


class RedisNotReadyError(RuntimeError):
    """Redis did not answer within ``REDIS_STARTUP_TIMEOUT``."""


class RedisConsumer:
    def __init__(self, cfg: Config, handlers: list[MessageHandler]) -> None:
        self._cfg = cfg
        self._handlers = handlers
        self._running = False
        self._client: aioredis.Redis | None = None
        self._streams = list(dict.fromkeys(cfg.redis.streams)) or ["messages"]
        self._state = "waiting_for_redis"

    @property
    def state(self) -> str:
        """``waiting_for_redis``, ``running``, ``stopped`` or ``failed``."""
        return self._state

    def mark_failed(self) -> None:
        if self._state != "stopped":
            self._state = "failed"

    async def _connect(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.Redis(
                host=self._cfg.redis.addr,
                port=self._cfg.redis.port,
                password=self._cfg.redis.password or None,
                decode_responses=True,
                socket_timeout=SOCKET_TIMEOUT_SECONDS,
            )
        return self._client

    async def _ensure_groups(self, client: aioredis.Redis) -> None:
        """Create the group where missing, at ``CONSUMER_START_ID`` (default ``$``),
        so a first start does not read out a long-lived stream's history."""
        group = self._cfg.consumer_group
        start_id = self._cfg.consumer_start_id
        for stream in self._streams:
            try:
                groups = await client.xinfo_groups(stream)
                if any(g["name"] == group for g in groups):
                    continue
            except aioredis.ResponseError:
                pass  # no such stream yet: mkstream creates it
            await client.xgroup_create(stream, group, id=start_id, mkstream=True)
            logger.info("created consumer group %s on stream %s at %s", group, stream, start_id)

    async def _resolve_payload(self, client: aioredis.Redis, message_id: str) -> Message | None:
        try:
            result = await client.execute_command("JSON.GET", message_id, "$")
            if result is None:
                logger.warning("JSON.GET returned None for %s", message_id)
                return None
            data = json.loads(result)
            if isinstance(data, list):
                data = data[0] if data else {}
            return Message.from_dict(data)
        except Exception:
            logger.exception("failed to resolve payload for %s", message_id)
            return None

    async def _startup_attempt(self) -> None:
        client = await self._connect()
        await client.ping()
        await self._ensure_groups(client)

    async def wait_ready(self, timeout: float) -> None:
        """Retry until Redis answers, with exponential backoff, for at most ``timeout``."""
        deadline = time.monotonic() + timeout
        backoff = STARTUP_BACKOFF_INITIAL_SECONDS
        attempt = 0
        while True:
            attempt += 1
            remaining = deadline - time.monotonic()
            try:
                await asyncio.wait_for(
                    self._startup_attempt(),
                    timeout=max(0.001, min(STARTUP_ATTEMPT_TIMEOUT_SECONDS, remaining)),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure is retried within the budget
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RedisNotReadyError(
                        f"redis at {self._cfg.redis.addr}:{self._cfg.redis.port} not ready "
                        f"after {attempt} attempts: {exc!r}"
                    ) from exc
                sleep_for = min(backoff, remaining)
                logger.warning(
                    "redis not ready, retrying",
                    extra={"attempt": attempt, "error": repr(exc), "next_sleep": sleep_for},
                )
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, STARTUP_BACKOFF_MAX_SECONDS)
                continue
            if attempt > 1:
                logger.info("redis ready after retries", extra={"attempts": attempt})
            return

    async def run(self) -> None:
        await self.wait_ready(self._cfg.redis_startup_timeout)
        client = await self._connect()
        group = self._cfg.consumer_group
        logger.info(
            "consumer starting",
            extra={
                "redis_addr": f"{self._cfg.redis.addr}:{self._cfg.redis.port}",
                "streams": self._streams,
                "consumer_group": group,
            },
        )
        self._running = True
        self._state = "running"
        while self._running:
            try:
                entries = await client.xreadgroup(
                    groupname=group,
                    consumername=self._cfg.consumer_name,
                    streams={s: ">" for s in self._streams},
                    count=10,
                    block=BLOCK_MS,
                )
                for stream_name, messages in entries or []:
                    for entry_id, fields in messages:
                        await self._process_entry(client, stream_name, group, entry_id, fields)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("error reading from streams")
                await asyncio.sleep(2)

    async def _process_entry(
        self, client: aioredis.Redis, stream: str, group: str, entry_id: str, fields: dict
    ) -> None:
        if self._too_old(entry_id):
            logger.info("skipped entry older than MAX_MESSAGE_AGE", extra={"stream": stream, "stream_id": entry_id})
            await client.xack(stream, group, entry_id)
            return
        message_id = fields.get("messageID", "")
        msg = await self._resolve_payload(client, message_id) if message_id else None
        if not message_id:
            logger.warning("stream entry %s has no messageID field", entry_id)
        if msg is not None:
            caught = CaughtMessage(message=msg, object_id=message_id, stream_id=entry_id, caught_at=datetime.now())
            for handler in self._handlers:
                try:
                    handler(caught)
                except Exception:
                    logger.exception("handler error")
        await client.xack(stream, group, entry_id)

    def _too_old(self, entry_id: str) -> bool:
        """True when the entry is older than ``MAX_MESSAGE_AGE`` (0 = never),
        measured from the stream ID, i.e. when Redis added it."""
        max_age = self._cfg.max_message_age
        if max_age <= 0:
            return False
        try:
            added_ms = int(entry_id.split("-", 1)[0])
        except ValueError:
            return False
        return time.time() - added_ms / 1000 > max_age

    async def shutdown(self) -> None:
        if self._state != "failed":
            self._state = "stopped"
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("consumer shut down")
