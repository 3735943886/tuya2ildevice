"""Minimal host: rustuya-bridge (MQTT) <-> tuya2ildevice <-> il-ha (MQTT). The only I/O in the project.

    python examples/host.py tuyadevices.json --bridge localhost:1883 --il localhost:1883 [--root rustuya] [--il-prefix il]

Needs paho-mqtt >= 2. One connection per broker (the same one twice if both are the same broker).
"""
import argparse
import json
import threading
import time

import paho.mqtt.client as mqtt

from tuya2ildevice import BridgeTopics, Hub, IlTopics, Schedule, Unschedule


def connect(spec: str, client_id: str) -> mqtt.Client:
    host, _, port = spec.partition(":")
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    c.connect_async(host, int(port or 1883))
    return c


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("devices")
    a.add_argument("--bridge", default="localhost:1883")
    a.add_argument("--il", default="localhost:1883")
    a.add_argument("--root", default="rustuya")
    a.add_argument("--il-prefix", default="il")
    a.add_argument("--source", default="tuya")
    args = a.parse_args()

    hub = Hub(json.load(open(args.devices)), bridge=BridgeTopics(args.root), il=IlTopics(args.il_prefix, args.source))
    side = {"bridge": connect(args.bridge, "tuya2il-bridge"), "il": connect(args.il, "tuya2il-il")}
    side["il"].will_set(hub.presence(False).topic, "offline", 1, True)     # M-12

    timers = {}                                    # (device id, name) -> threading.Timer

    def run(outs):
        for p in outs:
            if isinstance(p, Schedule):            # a converter asked to be woken up later
                if old := timers.pop((p.device_id, p.name), None):
                    old.cancel()
                t = timers[(p.device_id, p.name)] = threading.Timer(
                    p.after, lambda p=p: run(hub.on_timer(time.time(), p.device_id, p.name)))
                t.daemon = True
                t.start()
            elif isinstance(p, Unschedule):
                if old := timers.pop((p.device_id, p.name), None):
                    old.cancel()
            else:
                side[p.side].publish(p.topic, p.payload, p.qos, p.retain)

    def on_connect(client, _u, _f, _rc, _p=None):
        which = "bridge" if client is side["bridge"] else "il"
        for s in hub.subscriptions():
            if s.side == which:
                client.subscribe(s.topic, s.qos)
        if which == "il":
            run(hub.start())

    side["bridge"].on_connect = side["il"].on_connect = on_connect
    side["bridge"].on_message = lambda c, u, m: run(hub.on_bridge(time.time(), m.topic, m.payload, m.retain))
    side["il"].on_message = lambda c, u, m: run(hub.on_il(time.time(), m.topic, m.payload, m.retain))
    for c in {id(c): c for c in side.values()}.values():
        c.loop_start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        run(hub.stop())


if __name__ == "__main__":
    main()
