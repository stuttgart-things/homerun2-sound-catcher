"""Message types matching homerun-library Message struct."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    """Homerun message matching the Go homerun.Message struct."""

    title: str = ""
    message: str = ""
    severity: str = "info"
    author: str = "unknown"
    timestamp: str = ""
    system: str = "unknown"
    tags: str = ""
    assignee_address: str = ""
    assignee_name: str = ""
    artifacts: str = ""
    url: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Message:
        field_map = {
            "title": "title",
            "message": "message",
            "severity": "severity",
            "author": "author",
            "timestamp": "timestamp",
            "system": "system",
            "tags": "tags",
            "assigneeAddress": "assignee_address",
            "assigneeName": "assignee_name",
            "artifacts": "artifacts",
            "url": "url",
        }
        kwargs = {}
        for json_key, py_key in field_map.items():
            if json_key in data:
                kwargs[py_key] = data[json_key]
        return cls(**kwargs)


@dataclass
class CaughtMessage:
    """A message consumed from Redis Streams with metadata."""

    message: Message
    object_id: str = ""
    stream_id: str = ""
    caught_at: datetime = field(default_factory=datetime.now)
