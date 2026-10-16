from __future__ import annotations

import json
import time

from sound_catcher.config import Config
from sound_catcher.consumer import RedisConsumer


class FakeRedis:
    def __init__(self, payloads: dict) -> None:
        self.payloads = payloads
        self.acked = []

    async def execute_command(self, cmd, key, path):
        return self.payloads.get(key)

    async def xack(self, stream, group, entry_id):
        self.acked.append(entry_id)


def entry_id(age: float) -> str:
    return f"{int((time.time() - age) * 1000)}-0"


async def test_entries_are_resolved_handled_and_acked():
    seen = []
    consumer = RedisConsumer(Config(), [seen.append])
    client = FakeRedis({"m1": json.dumps([{"title": "disk full", "severity": "error", "system": "nas"}])})
    fresh = entry_id(1)
    await consumer._process_entry(client, "messages", "g", fresh, {"messageID": "m1"})
    assert seen[0].message.title == "disk full" and seen[0].object_id == "m1"
    assert client.acked == [fresh]


async def test_old_missing_and_unresolvable_entries_are_acked_unplayed():
    seen = []
    consumer = RedisConsumer(Config(max_message_age=60), [seen.append])
    client = FakeRedis({})
    await consumer._process_entry(client, "messages", "g", entry_id(120), {"messageID": "m1"})
    await consumer._process_entry(client, "messages", "g", entry_id(1), {})
    await consumer._process_entry(client, "messages", "g", entry_id(1), {"messageID": "gone"})
    assert seen == []
    assert len(client.acked) == 3


async def test_a_failing_handler_does_not_block_the_ack():
    def boom(_):
        raise RuntimeError("x")

    consumer = RedisConsumer(Config(), [boom])
    client = FakeRedis({"m": json.dumps({"title": "t"})})
    await consumer._process_entry(client, "messages", "g", entry_id(1), {"messageID": "m"})
    assert len(client.acked) == 1
