"""tuya2ildevice: the one place that interprets Tuya, sans-IO.

Converts a Tuya device (as rustuya / rustuya-bridge see it) to an ildevice and back:

* `TuyaDriver`: descriptor, live dps packets -> values/events, ildevice commands -> dps.
* `Hub`: the same, plus il-mqtt.md wire mapping on the IL side. It takes already-decoded bridge input
  (`Connected`/`Disconnected`/`Message`) via `on_bridge_message` and emits abstract `BridgeCommand`s for writes/reads
  — it has no idea rustuya-bridge's own MQTT topics exist, let alone that they're configurable. Interpreting the
  real bridge's wire format is the host's job (a `pyrustuyabridge`-based bridge client, e.g. in rustuya-local).
* `tuya`: the DP engine underneath (classification, value conversion, quirks).
"""
from .checks import Rejected, check_command
from .converters import Converter, CoverMotion, Result
from .driver import TuyaDriver, default_env, descriptor_of, schema_of
from .io import (
                 Absent,
                 Command,
                 Connected,
                 Descriptor,
                 Disconnected,
                 Event,
                 Message,
                 Reject,
                 SendMessage,
                 Timer,
                 Value,
)
from .mqtt import BridgeCommand, Hub, IlTopics, Publish, Schedule, Subscribe, Unschedule
from .overrides import OverrideError, from_v1, merge_all


def preload() -> None:
    """Read the bundled data files that are otherwise read on first use (quirks, platform tables); importing the package
    reads the rest. Both block: a host whose event loop must not (Home Assistant) imports and calls this in a worker
    thread before it drives devices."""
    from .tuya.quirks import load_quirks
    from .tuya.runtime import preload_tables

    load_quirks()
    preload_tables()


__all__ = [
    "Absent",
    "BridgeCommand",
    "Command",
    "Connected",
    "Converter",
    "CoverMotion",
    "Descriptor",
    "Disconnected",
    "Event",
    "Hub",
    "IlTopics",
    "Message",
    "OverrideError",
    "Publish",
    "Reject",
    "Rejected",
    "Result",
    "Schedule",
    "SendMessage",
    "Subscribe",
    "Timer",
    "TuyaDriver",
    "Unschedule",
    "Value",
    "check_command",
    "default_env",
    "descriptor_of",
    "from_v1",
    "merge_all",
    "preload",
    "schema_of",
]
