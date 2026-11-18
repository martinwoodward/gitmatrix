# The event bus: one bounded, sequence-numbered ring of device events (knob,
# plugin and mode changes, notifications, Bluetooth/WiFi, brightness/volume)
# plus a snapshot of the latest values. /api/v1/events long-polls it; the
# phase 6 MQTT client can subscribe() to the same events and read `state`.
#
# Events are observed, never consumed: the host still acts on every knob event
# normally. Emitting allocates one small dict, so it only happens when
# something changes (never per frame).

from gitmatrix import _compat, _log

RING = 64
POLL_MS = 50            # how often a long-poll checks for new events

ENCODER = "encoder"     # {action: turn|press|release|long_press, delta?, position}
PLUGIN = "plugin"       # {name, previous}
MODE = "mode"           # {mode: plugin|menu|notification, previous}
NOTIFICATION = "notification"   # {action: start|end, id, plugin?, dismissed?}
BLUETOOTH = "bluetooth"         # {connected, streaming}
WIFI = "wifi"           # {connected, ip}
BRIGHTNESS = "brightness"       # {value}
VOLUME = "volume"       # {value}
TYPES = (ENCODER, PLUGIN, MODE, NOTIFICATION, BLUETOOTH, WIFI, BRIGHTNESS, VOLUME)


def _boot_id():
    try:
        import os
        return int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
    except (ImportError, AttributeError, NotImplementedError):
        return _compat.ticks_ms() & 0x7FFFFFFF


class Bus:
    def __init__(self, size=RING):
        self.size = size
        self.ring = [None] * size
        self.seq = 0
        self.boot = _boot_id()      # changes every boot, so clients can spot a restart
        self.position = 0           # knob detents since boot
        self.state = {}             # latest value per key (plugin, mode, volume, ...)
        self.listeners = []
        self.waiters = 0            # long-polls waiting right now

    def subscribe(self, fn):
        """fn(event) for every event, called synchronously; keep it short."""
        if fn not in self.listeners:
            self.listeners.append(fn)

    def unsubscribe(self, fn):
        if fn in self.listeners:
            self.listeners.remove(fn)

    def emit(self, kind, data=None, now=None):
        self.seq += 1
        event = {"seq": self.seq, "type": kind, "t": _compat.ticks_ms() if now is None else now}
        if data:
            event.update(data)
        self.ring[self.seq % self.size] = event
        for fn in self.listeners:
            try:
                fn(event)
            except Exception as e:  # noqa: BLE001 - a listener must not break the emitter
                _log.log(f"events: listener failed {_compat.error_text(e)}")
        return event

    def change(self, key, value, data=None, now=None):
        """Record a new value for `key` and emit it as an event of that type,
        only if it differs from the last one. Allocation-free when unchanged."""
        state = self.state
        if key in state and state[key] == value:
            return None
        previous = state.get(key)
        state[key] = value
        out = {"previous": previous}
        if data is None:
            out["value"] = value
        else:
            out.update(data)
        return self.emit(key, out, now)

    def input(self, e):
        """Mirror an _input.Event (turn/press/release/long_press)."""
        if e.kind == "turn":
            self.position += e.value
            data = {"action": "turn", "delta": e.value, "position": self.position}
        else:
            data = {"action": e.kind, "position": self.position}
        self.state[ENCODER] = data
        return self.emit(ENCODER, data, e.t)

    def oldest(self):
        return max(1, self.seq - self.size + 1)

    def read(self, since=None, kinds=None, now=None):
        """Events after `since` (None: everything still buffered), oldest first.
        `next` is the seq to pass next time; `dropped` counts events that fell
        off the ring before the client got them; `reset` means `since` is from
        before a reboot (it is ahead of us) and everything buffered is sent."""
        now = _compat.ticks_ms() if now is None else now
        reset = False
        dropped = 0
        if since is None:
            since = 0
        elif since > self.seq:
            reset = True
            since = 0
        else:
            since = max(0, since)
            dropped = max(0, self.oldest() - since - 1)
        start = max(since + 1, self.oldest())
        events = []
        for s in range(start, self.seq + 1):
            e = self.ring[s % self.size]
            if e is None or e["seq"] != s or (kinds is not None and e["type"] not in kinds):
                continue
            out = {k: v for k, v in e.items() if k != "t"}
            out["age_ms"] = _compat.ticks_diff(now, e["t"])
            events.append(out)
        return {"events": events, "next": self.seq, "dropped": dropped, "reset": reset, "boot": self.boot}

    def pending(self, since, kinds=None):
        """Is there anything after `since` (of these kinds)? No allocation."""
        if since > self.seq:
            return True
        if self.seq == since:
            return False
        if kinds is None:
            return True
        for s in range(max(since + 1, self.oldest()), self.seq + 1):
            e = self.ring[s % self.size]
            if e is not None and e["seq"] == s and e["type"] in kinds:
                return True
        return False

    async def wait(self, since, kinds=None, timeout_ms=25000):
        """Wait (without blocking the loop) until there is an event after
        `since` or the timeout passes. Counts sleeps rather than reading the
        clock, so it ends even if ticks_ms is frozen (tests)."""
        self.waiters += 1
        try:
            for _ in range(max(0, timeout_ms) // POLL_MS):
                if self.pending(since, kinds):
                    return True
                await sleep_ms(POLL_MS)
            return self.pending(since, kinds)
        finally:
            self.waiters -= 1


async def sleep_ms(ms):
    import asyncio

    if hasattr(asyncio, "sleep_ms"):
        await asyncio.sleep_ms(ms)
    else:
        await asyncio.sleep(ms / 1000)


def parse_types(text):
    """'encoder,plugin' -> ("encoder", "plugin"); None/'' -> None (all)."""
    if not text:
        return None
    kinds = tuple(k.strip() for k in str(text).split(",") if k.strip())
    for k in kinds:
        if k not in TYPES:
            raise ValueError(f"unknown event type: {k}")
    return kinds or None
