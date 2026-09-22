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
from .driver import TuyaDriver, default_env, descriptor_of, schema_of
from .converters import Converter, CoverMotion, Result
from .mqtt import BridgeCommand, Hub, IlTopics, Publish, Schedule, Subscribe, Unschedule
from .overrides import OverrideError, from_v1, merge_all
from .io import (Absent, Command, Connected, Descriptor, Disconnected, Event, Message, Reject, SendMessage, Timer,
                 Value)

__all__ = ["Converter", "CoverMotion", "Result", "Schedule", "Unschedule", "OverrideError", "from_v1", "merge_all", "Hub", "BridgeCommand", "IlTopics", "Publish", "Subscribe", "TuyaDriver", "descriptor_of", "default_env", "schema_of", "check_command", "Rejected", "Connected", "Disconnected",
           "Message", "Command", "Timer", "Descriptor", "Value", "Absent", "Event", "SendMessage", "Reject"]
