"""What rustuya-bridge says about itself: it publishes its configuration, retained, on `{root}/bridge/config`. The topic
templates in it (they can be changed on the bridge) tell the host where events and commands really are; see
`BridgeTopics.from_config`."""
from __future__ import annotations

import asyncio
import json
import logging

from .transport import Message, Transport

_LOGGER = logging.getLogger(__name__)


async def read_bridge_config(transport: Transport, root: str, timeout: float = 3.0) -> dict | None:
    """The retained `{root}/bridge/config`, or None if the bridge has not published one within `timeout` (it is not
    running yet, or it is an older bridge): the caller then uses the default layout."""
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_message(msg: Message) -> None:
        if got.done():
            return
        try:
            body = json.loads(msg.payload)
        except ValueError:
            _LOGGER.warning("the bridge's config on %s is not JSON", msg.topic)
            return
        if isinstance(body, dict):
            got.set_result(body)

    unsub = await transport.subscribe(f"{root}/bridge/config", on_message)
    try:
        return await asyncio.wait_for(got, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        unsub()
