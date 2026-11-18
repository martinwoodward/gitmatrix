# Minimal CPython stand-ins for the device's built-in modules and globals, enough
# to run the GitMatrix runtime and plugins on a laptop. Images really store
# pixels (0xAABBGGRR words, like the device) so blits and draws can be asserted.

import builtins
import sys
import time
import types

W, H = 53, 7


class Pen:
    def __init__(self, r, g, b, a=255):
        self.rgba = (int(r) & 255, int(g) & 255, int(b) & 255, int(a) & 255)

    def word(self):
        r, g, b, a = self.rgba
        return (a << 24) | (b << 16) | (g << 8) | r

    def __repr__(self):
        return f"Pen{self.rgba}"


def _oklch(l, c, h, a=255):  # noqa: E741 - mirrors the device signature
    # not a real conversion; just deterministic and non-black when bright
    return Pen(l, (l + h) % 256, (c + h) % 256, a)


color = types.SimpleNamespace(rgb=lambda r, g, b, a=255: Pen(r, g, b, a), oklch=_oklch)


class vec2:  # noqa: N801
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y


class rect:  # noqa: N801
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


class mat3:  # noqa: N801
    def __init__(self):
        self.ops = []

    def translate(self, x, y):
        self.ops.append(("t", x, y))
        return self

    def scale(self, x, y=None):
        self.ops.append(("s", x, x if y is None else y))
        return self


class Shape:
    def __init__(self, *args):
        self.args = args
        self.transform = None


shape = types.SimpleNamespace(circle=lambda *a: Shape("circle", *a), rectangle=lambda *a: Shape("rect", *a))


class Font:
    def __init__(self, name, height=5, advance=4):
        self.name = name
        self.height = height
        self.advance = advance


font = types.SimpleNamespace(sins=Font("sins", 5, 4), match=Font("match", 7, 5), kobold=Font("kobold", 5, 3))


class image(bytearray):  # noqa: N801 - device name
    """53x7 RGBA framebuffer that supports memoryview() like the real one."""

    def __init__(self, width=W, height=H):
        super().__init__(width * height * 4)
        self.width = width
        self.height = height
        self.pen = Pen(255, 255, 255)
        self.font = font.sins
        self.alpha = 255
        self.antialias = 0
        self.texts = []
        self.shapes = 0

    @property
    def words(self):
        return memoryview(self).cast("I")

    def pixel(self, x, y):
        return self.words[x + y * self.width]

    def _fill(self, x, y, w, h):
        word = self.pen.word() if isinstance(self.pen, Pen) else 0xFFFFFFFF
        px = self.words
        for yy in range(max(0, int(y)), min(self.height, int(y + h))):
            for xx in range(max(0, int(x)), min(self.width, int(x + w))):
                px[xx + yy * self.width] = word

    def clear(self):
        self._fill(0, 0, self.width, self.height)

    def rectangle(self, x, y, w, h):
        self._fill(x, y, w, h)

    def line(self, x1, y1, x2, y2):
        self._fill(min(x1, x2), min(y1, y2), abs(x2 - x1) + 1, abs(y2 - y1) + 1)

    def measure_text(self, text, size=None):
        f = self.font
        return (len(text) * f.advance, size or f.height)

    def text(self, text, pos, size=None):
        self.texts.append((text, pos.x, pos.y))
        w, h = self.measure_text(text, size)
        self._fill(pos.x, pos.y, w - 1, 1)

    def shape(self, s):
        self.shapes += 1

    def blur(self, amount):
        pass

    def blit(self, src, a, b=None):
        if isinstance(a, rect):
            dx, dy = int(b.x), int(b.y)
            sx, sy, sw, sh = int(a.x), int(a.y), int(a.w), int(a.h)
        else:
            dx, dy = (int(a.x), int(a.y)) if b is None else (int(a), int(b))
            sx, sy, sw, sh = 0, 0, src.width, src.height
        sp, dp = src.words, self.words
        for y in range(sh):
            ty = dy + y
            if not 0 <= ty < self.height:
                continue
            for x in range(sw):
                tx = dx + x
                if 0 <= tx < self.width:
                    dp[tx + ty * self.width] = sp[sx + x + (sy + y) * src.width]

    @staticmethod
    def load(data):
        return image(8, 8)


class Display:
    def __init__(self):
        self.updates = 0
        self.brightness = 1.0

    def update(self, *args):
        self.updates += 1

    def set_brightness(self, v):
        self.brightness = v

    def get_brightness(self):
        return self.brightness


class FakeBT:
    """bt_audio stand-in. Set .streaming_now / .bins to drive the audio API."""

    def __init__(self):
        self.name = None
        self.streaming_now = False
        self.is_connected = False
        self.seq = 0
        self.bins = [0] * 512
        self.vol = 64
        self.keys = b""
        self.loaded_keys = None
        self.keys_dirty = False
        self.pairable = None

    def init(self, name="GitMatrix"):
        self.name = name

    def load_link_keys(self, data):
        self.loaded_keys = bytes(data)
        return True

    def link_keys_dirty(self):
        return self.keys_dirty

    def link_keys(self):
        return self.keys

    def fft_seq(self):
        return self.seq

    def fft_into(self, buf):
        for i in range(min(len(buf), len(self.bins))):
            buf[i] = self.bins[i]
        return True

    def streaming(self):
        return self.streaming_now

    def connected(self):
        return self.is_connected

    def volume(self):
        return self.vol

    def set_volume(self, v):
        self.vol = v

    def push(self, bins):
        self.bins = list(bins) + [0] * (512 - len(bins))
        self.seq += 1

    def set_pairable(self, on):
        self.pairable = bool(on)


class WLAN:
    """network.WLAN stand-in: connect() never blocks; tests flip .up / .code."""

    SEC_WPA2 = 4

    def __init__(self, net, iface):
        self.net = net
        self.iface = iface
        self.on = False
        self.settings = {}
        self.joins = []          # (ssid, password) per connect()
        self.disconnects = 0
        self.up = False
        self.code = 0
        self.result = 1          # status() after connect(): 1 connecting, -2 not found, -3 bad password
        self.addr = ("192.168.1.42", "255.255.255.0", "192.168.1.1", "192.168.1.1")
        self.found = []
        self.scans = 0

    def active(self, on=None):
        if on is None:
            return self.on
        self.on = bool(on)
        if not on:
            self.up = False
        return None

    def config(self, *args, **kwargs):
        if args:
            return self.settings.get(args[0])
        self.settings.update(kwargs)
        return None

    def connect(self, ssid, password=None):
        if not self.on:
            raise OSError("interface not active")
        self.joins.append((ssid, password))
        self.code = self.result

    def disconnect(self):
        self.disconnects += 1
        self.up = False

    def isconnected(self):
        return self.on and self.up

    def status(self):
        return 3 if self.isconnected() else self.code

    def ifconfig(self):
        return self.addr

    def scan(self):
        if not self.on:
            raise OSError("interface not active")
        self.scans += 1
        return list(self.found)


class FakeNetwork:
    """The `network` module: one STA and one AP WLAN, and the hostname."""

    STA_IF = 0
    AP_IF = 1

    def __init__(self):
        self.name = None
        self.sta = WLAN(self, 0)
        self.ap = WLAN(self, 1)

    def hostname(self, name=None):
        if name is None:
            return self.name
        self.name = name
        return None

    def WLAN(self, iface):  # noqa: N802 - device name
        return self.sta if iface == self.STA_IF else self.ap


class Mem32(dict):
    def __getitem__(self, addr):
        return self.get(addr, 0)


class WDT:
    created = []

    def __init__(self, id=0, timeout=5000):  # noqa: A002
        self.timeout = timeout
        self.feeds = 0
        WDT.created.append(self)

    def feed(self):
        self.feeds += 1


class Pin:
    IN = 0
    PULL_UP = 1

    def __init__(self, n, mode=None, pull=None):
        self.n = n
        self.level = 1

    def value(self):
        return self.level


class Encoder:
    def __init__(self, *args, **kwargs):
        self.count = 0

    def position(self):
        return self.count

    def delta(self):
        d, self.count = self.count, 0
        return d


def _ptr32(buf):
    return memoryview(buf).cast("I")


def _ptr8(buf):
    return memoryview(buf).cast("B")


def install():
    """Register the stub modules and the device globals in builtins."""
    machine = types.ModuleType("machine")
    machine.mem32 = Mem32()
    machine.WDT = WDT
    machine.Pin = Pin
    machine.WDT_RESET = 3
    machine.PWRON_RESET = 1
    machine.cause = 1
    machine.reset_cause = lambda: machine.cause

    def _reset():
        raise AssertionError("machine.reset() must never be called (Phase 0)")

    machine.reset = _reset

    def _idle():
        if WDT.created:
            raise SystemExit("watchdog reboot")

    machine.idle = _idle
    machine.unique_id = lambda: b"\x01\x02\x03\x04\x05\x06\x07\x08"
    sys.modules["machine"] = machine

    mp = types.ModuleType("micropython")
    mp.const = lambda x: x
    mp.viper = mp.native = lambda f: f
    mp.mem_info = lambda *a: None
    sys.modules["micropython"] = mp

    enc = types.ModuleType("encoder")
    enc.Encoder = Encoder
    sys.modules["encoder"] = enc

    fake = bt = FakeBT()
    sys.modules["bt_audio"] = fake

    sys.modules["contribution_widget"] = types.ModuleType("contribution_widget")

    if not hasattr(time, "ticks_ms"):
        time.ticks_ms = lambda: int(time.monotonic() * 1000)
        time.ticks_diff = lambda a, b: a - b
        time.ticks_add = lambda a, b: a + b

    g = {
        "color": color, "vec2": vec2, "rect": rect, "mat3": mat3, "shape": shape,
        "font": font, "image": image, "ptr32": _ptr32, "ptr8": _ptr8,
        "screen": image(), "display": Display(), "bt_audio": bt,
        "brush": types.SimpleNamespace(pattern=lambda *a: Pen(255, 255, 255)),
    }
    for k, v in g.items():
        setattr(builtins, k, v)
    return fake
