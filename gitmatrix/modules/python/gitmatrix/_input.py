# Encoder + button input. The host owns the hardware and records events in a
# small sequence-numbered ring so the host, the menu and plugins with
# "input": true all see the same events without stealing them from each other.

from gitmatrix import _compat

TURN = "turn"
PRESS = "press"
RELEASE = "release"
LONG_PRESS = "long_press"

DEBOUNCE_MS = 30
LONG_PRESS_MS = 1000
RING = 16

ENCODER_PINS = (22, 21)
BUTTON_PIN = 20


class Event:
    def __init__(self, seq, kind, value, t):
        self.seq = seq
        self.kind = kind
        self.value = value   # turn: signed detents (+1 clockwise); press/release: 0
        self.t = t

    def __repr__(self):
        return f"Event({self.seq}, {self.kind!r}, {self.value})"


class Input:
    def __init__(self, encoder=None, button=None):
        self.encoder = encoder
        self.button = button
        self.ring = [None] * RING
        self.seq = 0
        self._down = False
        self._down_at = 0
        self._changed = 0
        self._long_sent = False
        if button is not None:
            self._down = button.value() == 0

    @classmethod
    def from_hardware(cls):
        enc = btn = None
        try:
            from encoder import Encoder
            enc = Encoder(1, 0, ENCODER_PINS)
        except Exception as e:  # noqa: BLE001 - run without a knob rather than not at all
            print(f"input: no encoder ({_compat.error_text(e)})")
        try:
            from machine import Pin
            btn = Pin(BUTTON_PIN, Pin.IN, Pin.PULL_UP)
        except Exception as e:  # noqa: BLE001
            print(f"input: no button ({_compat.error_text(e)})")
        return cls(enc, btn)

    def push(self, kind, value=0):
        self.seq += 1
        self.ring[self.seq % RING] = Event(self.seq, kind, value, _compat.ticks_ms())
        return self.seq

    def poll(self):
        """Read the hardware. Returns the newest sequence number."""
        now = _compat.ticks_ms()
        if self.encoder is not None:
            d = self.encoder.delta()
            if d:
                self.push(TURN, d)
        if self.button is not None:
            down = self.button.value() == 0
            if down != self._down and _compat.ticks_diff(now, self._changed) > DEBOUNCE_MS:
                self._down = down
                self._changed = now
                if down:
                    self._down_at = now
                    self._long_sent = False
                    self.push(PRESS)
                else:
                    self.push(RELEASE)
            elif self._down and not self._long_sent and _compat.ticks_diff(now, self._down_at) > LONG_PRESS_MS:
                self._long_sent = True
                self.push(LONG_PRESS)
        return self.seq

    def held(self):
        return self._down

    def since(self, seq, kinds=None):
        """Events newer than seq, oldest first. Older ones may have dropped off the ring."""
        out = []
        start = max(seq + 1, self.seq - RING + 1)
        for s in range(start, self.seq + 1):
            e = self.ring[s % RING]
            if e is not None and e.seq == s and (kinds is None or e.kind in kinds):
                out.append(e)
        return out


class PluginInput:
    """gitmatrix.input for plugins. The button is reserved for the menu, so
    plugins see turns; ask for "input": true in package.json to get them."""

    def __init__(self, source):
        self.source = source
        self._cursors = {}

    def _key(self):
        from gitmatrix import _runtime
        return _runtime.current_name()

    def events(self):
        key = self._key()
        cursor = self._cursors.get(key, self.source.seq)
        self._cursors[key] = self.source.seq
        return self.source.since(cursor, (TURN,))

    def turns(self):
        """Sum of detents since the last call."""
        total = 0
        for e in self.events():
            total += e.value
        return total

    def reset(self, name=None):
        self._cursors[name if name is not None else self._key()] = self.source.seq
