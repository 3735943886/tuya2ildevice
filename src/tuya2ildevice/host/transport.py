"""What the host needs from a message transport: subscribe to a topic filter, publish a message.

A structural type: anything with these two methods works (paho below, the in-process broker in `memory.py`, a test
double). Nothing here knows about Tuya or IL.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes | str
    retain: bool = False
    """True for a retained message replayed on subscribe, False for one published live."""


Callback = Callable[[Message], "Awaitable[None] | None"]
Unsubscribe = Callable[[], None]


class Transport(Protocol):
    async def subscribe(self, topic: str, callback: Callback, qos: int = 1) -> Unsubscribe: ...

    async def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None: ...
