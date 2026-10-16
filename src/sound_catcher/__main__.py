"""Entry point for homerun2-sound-catcher."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

import uvicorn

from sound_catcher.audio.library import SoundLibrary
from sound_catcher.audio.player import LocalPlayer, detect_player_cmd
from sound_catcher.audio.s3sync import S3SoundSync, create_s3_client
from sound_catcher.audio.tts import create_synthesizer, write_silence
from sound_catcher.config import Config, load_config, setup_logging
from sound_catcher.consumer import RedisConsumer
from sound_catcher.dispatcher import Dispatcher
from sound_catcher.history import History
from sound_catcher.profile import load_profile
from sound_catcher.web.app import create_app

logger = logging.getLogger("sound_catcher")


class _Server(uvicorn.Server):
    """A uvicorn server that leaves the signal handlers to us, so a signal
    starts one shutdown, not two (led-catcher #105)."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def build_player(cfg: Config) -> LocalPlayer | None:
    """The local sink, or None when SOUND_SINKS leaves it out. Raises
    ValueError when it is wanted and nothing can play."""
    if not cfg.local:
        return None
    preroll = None
    if cfg.preroll > 0:
        preroll = write_silence(cfg.tts_cache_dir / f"preroll-{int(cfg.preroll * 1000)}ms.wav", cfg.preroll)
    return LocalPlayer(cfg.player_cmd or detect_player_cmd(), preroll=preroll)


def build_sound_sync(cfg: Config) -> S3SoundSync | None:
    """The S3 sync, or None without SOUNDS_S3_BUCKET. Raises ValueError when
    the bucket is set and boto3 is missing."""
    if not cfg.s3.enabled:
        return None
    client = create_s3_client(cfg.s3.endpoint, cfg.s3.region, cfg.s3.anonymous)
    return S3SoundSync(
        client,
        cfg.s3.bucket,
        cfg.s3.cache_dir or cfg.tts_cache_dir / "s3-sounds",
        prefix=cfg.s3.prefix,
        interval=cfg.s3.interval,
    )


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _handler() -> None:
        logger.info("received shutdown signal")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handler)


async def _run(cfg: Config) -> int:
    logger.info(
        "starting homerun2-sound-catcher",
        extra={"version": cfg.version, "commit": cfg.commit, "date": cfg.date, "mode": cfg.mode, "sinks": cfg.sinks},
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)

    try:
        profile = load_profile(cfg.profile_path, rules_expected=cfg.mode == "full")
        synthesizer = create_synthesizer(cfg.tts_voice, cfg.tts_cache_dir, cfg.tts_voices_dir)
        player = build_player(cfg)
        sound_sync = build_sound_sync(cfg)
    except ValueError as exc:
        logger.error("invalid configuration: %s", exc)
        return 1

    dirs = [("bundled", cfg.sounds_dir)]
    if sound_sync is not None:
        # In front of the bundled clips, so an upload can replace one.
        dirs.insert(0, ("s3", sound_sync.dest))
        sound_sync.start()
        logger.info("sounds: syncing %s every %ds", sound_sync.describe(), cfg.s3.interval)
    history = History(cfg.history_size)
    dispatcher = Dispatcher(
        profile,
        history,
        SoundLibrary(dirs),
        synthesizer=synthesizer,
        player=player,
        browser=cfg.browser,
        dedup_window=cfg.dedup_window,
    )

    consumer = None
    if cfg.mode == "full":
        consumer = RedisConsumer(cfg, [dispatcher.submit])
    else:
        logger.info("standalone mode: no Redis, only POST /play makes a sound")
    if not cfg.api.writable:
        logger.info("no SOUND_API_TOKEN: /play, mute and speaker replay are off")

    def health() -> dict:
        body: dict = {"sinks": dispatcher.sinks}
        if consumer is not None:
            body["consumer"] = consumer.state
            if consumer.state == "failed":
                body["status"] = "unhealthy"
        if sound_sync is not None:
            # Reported, never unhealthy: the sounds synced last still play.
            body["s3"] = sound_sync.status.to_json()
        if player is not None:
            body["player"] = "running" if player.alive else "stopped"
            if not player.alive:
                body["status"] = "unhealthy"
        return body

    app = create_app(
        dispatcher,
        history,
        cfg.api,
        history_size=cfg.history_size,
        version=cfg.version,
        commit=cfg.commit,
        date=cfg.date,
        health=health,
        stop=stop,
    )
    server = _Server(uvicorn.Config(app, host="0.0.0.0", port=cfg.health_port, log_level="warning"))  # nosec B104
    server_task = asyncio.create_task(server.serve())
    logger.info("player page on port %d", cfg.health_port)

    waits = {asyncio.create_task(stop.wait()), server_task}
    consumer_task = asyncio.create_task(consumer.run()) if consumer is not None else None
    if consumer_task is not None:
        waits.add(consumer_task)
    await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)

    clean = stop.is_set()
    if not clean:
        what = "consumer" if consumer_task is not None and consumer_task.done() else "web server"
        if consumer is not None:
            consumer.mark_failed()
        logger.error("%s stopped unexpectedly, exiting", what)
    stop.set()
    if consumer is not None:
        await consumer.shutdown()
    if consumer_task is not None and not consumer_task.done():
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await consumer_task
    dispatcher.close()
    if sound_sync is not None:
        sound_sync.close()
    server.should_exit = True
    if not server_task.done():
        await server_task
    logger.info("sound-catcher exited" + ("" if clean else " after a failure"))
    return 0 if clean else 1


def main() -> None:
    try:
        cfg = load_config()
    except ValueError as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        sys.exit(1)
    setup_logging(cfg)
    sys.exit(asyncio.run(_run(cfg)))


if __name__ == "__main__":
    main()
