# A glowing clock. Shows "--:--" until the RTC has been set (by NTP over WiFi,
# or mpremote, which sets it on connect).

import math
import time

import gitmatrix
from gitmatrix import _compat
from gitmatrix.glow import OX, OY, H, W, Ambient, Canvas, blob, ease_out_back, text, text_size

HUE = 200
SYNCED_AFTER = 1700000000  # an RTC earlier than late 2023 hasn't been set

canvas = None
ambient = None
face = None
adv = 4
shown = None
prev = None
changed_at = 0
shown_at = 0


def now():
    t = time.time()
    if t < SYNCED_AFTER:
        return None
    cfg = gitmatrix.settings()
    tm = time.localtime(t + int(float(cfg.get("utc_offset", 0)) * 3600))
    h, m, s = tm[3], tm[4], tm[5]
    suffix = ""
    if not cfg.get("clock_24h", True):
        suffix = "AM" if h < 12 else "PM"
        h = h % 12 or 12
    return "%02d%02d" % (h, m), suffix, s


def on_show():
    global canvas, ambient, face, adv, shown, shown_at
    if canvas is None:
        canvas = Canvas()
        ambient = Ambient(HUE, count=5, alpha=50, spread=40)
        face = getattr(font, "match", font.sins)
        adv = int(text_size(canvas.image, "0", face)[0]) + 1
    shown = None
    shown_at = _compat.ticks_ms()


def _layout(c, suffix):
    colon = 3
    small = getattr(font, "kobold", font.sins)
    width = adv * 4 + colon + (int(text_size(c, suffix, small)[0]) + 2 if suffix else 0)
    x = (W - width) // 2
    return [x, x + adv, x + adv * 2 + colon, x + adv * 3 + colon], x + adv * 2, x + adv * 4 + colon + 2, small


def _hue():
    return (HUE + int(30 * math.sin(_compat.ticks_ms() / 9000))) % 256


def _digits(c, digits, suffix, pen):
    global shown, prev, changed_at
    ms = _compat.ticks_ms()
    if digits != shown:
        prev, shown, changed_at = shown, digits, ms
    p = min(1.0, _compat.ticks_diff(ms, changed_at) / 450)
    p = ease_out_back(p) if p < 1 else 1.0
    xs, colon_x, suffix_x, small = _layout(c, suffix)
    for i, ch in enumerate(digits):
        if prev and prev[i] != ch and p < 1:
            text(c, prev[i], xs[i], face, pen, -int(round(p * H)))
            text(c, ch, xs[i], face, pen, H - int(round(p * H)))
        else:
            text(c, ch, xs[i], face, pen)
    if ms % 1000 < 600:
        c.pen = pen
        c.rectangle(OX + colon_x, OY + 2, 1, 1)
        c.rectangle(OX + colon_x, OY + 4, 1, 1)
    if suffix:
        text(c, suffix, suffix_x, small, pen)


def update():
    if canvas is None:
        on_show()
    t = _compat.ticks_diff(_compat.ticks_ms(), shown_at)
    c = canvas.clear()
    ambient.draw(c)
    r = now()
    if r is not None:
        digits, suffix, sec = r
        cx = OX - 3 + (sec + (_compat.ticks_ms() % 1000) / 1000) / 60 * (W + 6)
        blob(c, cx, OY + 6, 3, color.oklch(220, 200, (_hue() + 60) % 256, 120))
        _digits(c, digits, suffix, color.oklch(200, 220, _hue(), 200))
    canvas.blur(2)
    if r is None:
        a = int(128 + 127 * math.sin(t / 250))
        w = int(text_size(c, "--:--", face)[0])
        text(c, "--:--", (W - w) // 2, face, color.oklch(200, 150, HUE, a))
    else:
        _digits(c, r[0], r[1], color.oklch(245, 90, _hue()))
    canvas.show()
