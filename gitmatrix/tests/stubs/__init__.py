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
        if isinstance(a, rect) and isinstance(b, rect) and (int(b.w), int(b.h)) != (int(a.w), int(a.h)):
            # scaled blit, nearest neighbour (picovector scales to the target rect)
            sp, dp = src.words, self.words
            for y in range(int(b.h)):
                ty = int(b.y) + y
                for x in range(int(b.w)):
                    tx = int(b.x) + x
                    if 0 <= tx < self.width and 0 <= ty < self.height:
                        sx = int(a.x) + x * int(a.w) // int(b.w)
                        sy = int(a.y) + y * int(a.h) // int(b.h)
                        dp[tx + ty * self.width] = sp[sx + sy * src.width]
            return
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
        if isinstance(data, (bytes, bytearray, memoryview)) and bytes(data[:8]) == PNG_SIG:
            w, h, rows = decode_png(bytes(data))
            img = image(w, h)
            px = img.words
            for y, row in enumerate(rows):
                for x, (r, g, b, a) in enumerate(row):
                    px[x + y * w] = (a << 24) | (b << 16) | (g << 8) | r
            return img
        return image(8, 8)


PNG_SIG = b"\x89PNG\r\n\x1a\n"


def decode_png(data):
    """Tiny PNG decoder (8-bit grey/RGB/RGBA/palette, no interlace) so tests can
    use real PNG bytes. Returns (w, h, rows of (r, g, b, a))."""
    import struct
    import zlib

    pos, idat, palette, trns = 8, b"", None, None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or interlace:
                raise ValueError("stub PNG: 8-bit, non-interlaced only")
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, n, 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * bpp
    prev = bytearray(stride)
    rows = []
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        row = []
        for x in range(w):
            px = line[x * bpp:(x + 1) * bpp]
            if ctype == 0:
                row.append((px[0], px[0], px[0], 255))
            elif ctype == 2:
                row.append((px[0], px[1], px[2], 255))
            elif ctype == 3:
                r, g, b = palette[px[0]]
                row.append((r, g, b, trns[px[0]] if trns is not None and px[0] < len(trns) else 255))
            elif ctype == 4:
                row.append((px[0], px[0], px[0], px[1]))
            else:
                row.append(tuple(px))
        rows.append(row)
    return w, h, rows


def make_png(w, h, pixel=lambda x, y: (255, 255, 255, 255)):
    """RGBA PNG bytes for tests; pixel(x, y) -> (r, g, b, a)."""
    import struct
    import zlib

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + bytes(c for x in range(w) for c in pixel(x, y)) for y in range(h))
    return (PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


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
        self.inits = 0
        self.deinits = 0

    def init(self, name="GitMatrix"):
        self.name = name
        self.inits += 1

    def deinit(self):
        self.name = None
        self.streaming_now = False
        self.is_connected = False
        self.deinits += 1

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
        Timer.fire_all()         # the radio is busy: only scheduled callbacks run
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


class Timer:
    """Soft virtual timer. On the device its callback is scheduled, so it still
    runs inside blocking C waits such as WLAN.scan(); fire_all() simulates that."""

    PERIODIC = 1
    ONE_SHOT = 0
    live = []

    def __init__(self, id=-1):  # noqa: A002
        self.callback = None
        self.period = None

    def init(self, mode=PERIODIC, period=-1, callback=None):
        self.period = period
        self.callback = callback
        Timer.live.append(self)

    def deinit(self):
        if self in Timer.live:
            Timer.live.remove(self)

    @classmethod
    def fire_all(cls):
        for t in list(cls.live):
            t.callback(t)


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 1

    def __init__(self, n, mode=None, pull=None):
        self.n = n
        self.level = 1
        Pin.history.append((n, "init"))

    def value(self, v=None):
        if v is None:
            return self.level
        self.level = int(v)
        Pin.history.append((self.n, int(v)))
        return None

    history = []


class I2S:
    """machine.I2S stand-in: records what was written. drain() yields, like the
    asyncio StreamWriter the device wraps around the real one."""

    TX = 0
    RX = 1
    MONO = 0
    STEREO = 1
    opened = []

    def __init__(self, id, sck=None, ws=None, sd=None, mode=TX, bits=16, format=STEREO, rate=22050, ibuf=8192):  # noqa: A002
        self.id = id
        self.rate = rate
        self.bits = bits
        self.format = format
        self.data = bytearray()
        self.closed = False
        self.writes = 0
        I2S.opened.append(self)

    def write(self, buf):
        self.data += bytes(buf)
        self.writes += 1
        return len(buf)

    async def drain(self):
        import asyncio
        await asyncio.sleep(0)

    def deinit(self):
        self.closed = True

    def samples(self):
        """Interleaved signed 16-bit samples written so far."""
        return list(memoryview(bytes(self.data)).cast("h"))


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


def _ptr16(buf):
    return memoryview(buf).cast("H")


def install():
    """Register the stub modules and the device globals in builtins."""
    machine = types.ModuleType("machine")
    machine.mem32 = Mem32()
    machine.WDT = WDT
    machine.Pin = Pin
    machine.Timer = Timer
    machine.I2S = I2S
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
        "font": font, "image": image, "ptr32": _ptr32, "ptr8": _ptr8, "ptr16": _ptr16,
        "screen": image(), "display": Display(), "bt_audio": bt,
        "brush": types.SimpleNamespace(pattern=lambda *a: Pen(255, 255, 255)),
    }
    for k, v in g.items():
        setattr(builtins, k, v)
    return fake
