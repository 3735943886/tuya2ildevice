"""Run a `Hub` for real: MQTT (or in-process) transports, timers, Last Will presence, reconnects, runtime device changes,
following rustuya-manager's `tuyadevices.json` and a directory of user overrides. The Hub itself stays sans-IO; this is the I/O around it.

    from tuya2ildevice.host import MqttTransport, Runner        # MqttTransport needs `pip install tuya2ildevice[host]`
"""
from .devices import DeviceWatcher, load_devices, parse_devices
from .memory import InProcessTransport
from .overrides import OverrideSet, OverrideWatcher, load_overrides
from .runner import Runner
from .transport import Message, Transport

__all__ = ["DeviceWatcher", "InProcessTransport", "Message", "OverrideSet", "OverrideWatcher", "Runner", "Transport",
           "load_devices", "load_overrides", "parse_devices"]


def __getattr__(name: str):                  # MqttTransport imports paho, which is an optional extra
    if name == "MqttTransport":
        from .mqtt import MqttTransport
        return MqttTransport
    raise AttributeError(name)
