# Widget apps shown in rotation. Each app draws into a 73x21 canvas (the 53x7 display
# plus a margin so blurred glow doesn't clip at the edges). Rendering is split into a
# glow pass (blurred) and a crisp pass drawn on top, which gives the ambient bloom.

import json
import math
import random
import time

import easing

import config

W, H = 53, 7
OX, OY = 10, 7  # display origin within the canvas
CW, CH = W + OX * 2, H + OY * 2

BLACK = color.rgb(0, 0, 0)
WHITE = color.rgb(255, 255, 255)

_circle = shape.circle(0, 0, 1.0)


def blob(c, x, y, r, pen):
    c.pen = pen
    _circle.transform = mat3().translate(x, y).scale(r)
    c.shape(_circle)


def text_width(text, font):
    screen.font = font
    return int(screen.measure_text(text)[0])


def text(c, s, x, font, pen, y_off=0):
    c.font = font
    th = c.measure_text(s)[1]
    c.pen = pen
    c.text(s, vec2(OX + x, OY + (H - th) // 2 + y_off))


def format_count(n, approx=False, max_width=W, font=None):
    full = "{:,}".format(n)
    if not approx and (font is None or text_width(full, font) <= max_width):
        return full
    for div, suffix in ((1000000000, "B"), (1000000, "M"), (1000, "K")):
        if n >= div:
            v = n / div
            s = ("%.2f" if v < 10 else "%.1f" if v < 100 else "%.0f") % v
            if "." in s:
                s = s.rstrip("0").rstrip(".")
            return s + suffix
    return str(n)


class Ambient:
    """Slow drifting soft blobs that sit behind everything in the glow pass."""

    def __init__(self, hue, count=5, alpha=45, hue_spread=20):
        self.hue = hue
        self.alpha = alpha
        self.blobs = []
        for i in range(count):
            self.blobs.append([random.uniform(0, CW), random.uniform(OY, OY + H),
                               random.uniform(3, 6), random.uniform(-1, 1) * 0.12,
                               random.uniform(-1, 1) * 0.04, random.uniform(-hue_spread, hue_spread)])

    def draw(self, c):
        for b in self.blobs:
            b[0] += b[3]
            b[1] += b[4]
            if not 0 <= b[0] <= CW:
                b[3] = -b[3]
            if not OY - 2 <= b[1] <= OY + H + 2:
                b[4] = -b[4]
            blob(c, b[0], b[1], b[2], color.oklch(140, 200, (self.hue + b[5]) % 256, self.alpha))


class Sparkles:
    def __init__(self, hue, rate=0.08):
        self.hue = hue
        self.rate = rate
        self.items = []  # x, y, life

    def burst(self, n, x0=0, x1=W):
        for _ in range(n):
            self.items.append([random.randint(x0, x1 - 1), random.randint(0, H - 1), 1.0])

    def update(self, x0=0, x1=W):
        if random.random() < self.rate:
            self.burst(1, x0, x1)
        for s in self.items:
            s[2] -= 0.04
        self.items = [s for s in self.items if s[2] > 0]

    def draw(self, c):
        for x, y, life in self.items:
            c.pen = color.oklch(255, 120, self.hue, int(255 * life))
            c.rectangle(OX + x, OY + y, 1, 1)


class App:
    hue = 140

    def __init__(self, data):
        self.data = data

    def enter(self):
        pass

    def render(self, c, t):
        # t: ms since this app was entered
        c.pen = BLACK
        c.clear()
        self.glow(c, t)
        c.blur(2)
        self.crisp(c, t)

    def glow(self, c, t):
        pass

    def crisp(self, c, t):
        pass


# --- Graph ------------------------------------------------------------------------------

LEVEL_RGB = [
    (0, 6, 2),
    (0, 45, 14),
    (0, 100, 30),
    (0, 170, 50),
    (70, 255, 110),
]
TWINKLE_MS = 900


class Graph(App):
    hue = 100

    def __init__(self, data):
        super().__init__(data)
        self.version = -1
        self.cells = []
        self.twinkles = {}  # cell index -> start ms
        self.ambient = Ambient(self.hue, count=4, alpha=25)

    def load(self):
        self.version = self.data.version
        try:
            with open("/contrib_data.json") as f:
                weeks = json.load(f)["weeks"][-W:]
        except (OSError, ValueError, KeyError):
            self.cells = []
            return
        # Each square breathes at its own random speed and phase
        self.cells = [(x, d["weekday"], min(4, d["level"]),
                       random.uniform(0, 6.28), random.uniform(0.0012, 0.003))
                      for x, w in enumerate(weeks) for d in w["contribution_days"]]

    def enter(self):
        if self.version != self.data.version or not self.cells:
            self.load()

    def _cols(self, t):
        return min(W, t // 12 + 1)

    def _update_twinkles(self, t):
        for i, start in list(self.twinkles.items()):
            if t - start > TWINKLE_MS or t < start:
                del self.twinkles[i]
        if self.cells and random.random() < 0.12:
            i = random.randrange(len(self.cells))
            if self.cells[i][2] > 0:
                self.twinkles[i] = t

    def _draw_cells(self, c, t, glow):
        cols = self._cols(t)
        n = len(self.cells)
        for i in range(n):
            x, y, level, phase, speed = self.cells[i]
            if x >= cols or (glow and level < 2 and i not in self.twinkles):
                continue
            if i == n - 1:
                # Today pulses more strongly
                c.pen = color.oklch(150 + int(90 * (0.5 + 0.5 * math.sin(t / 300))), 220, self.hue)
            else:
                r, g, b = LEVEL_RGB[level]
                f = 0.72 + 0.28 * math.sin(t * speed + phase) if level else 1.0
                tw = self.twinkles.get(i)
                if tw is not None:
                    # Flare towards bright mint and fade back
                    k = math.sin(math.pi * (t - tw) / TWINKLE_MS)
                    r, g, b = r + (180 - r) * k, g + (255 - g) * k, b + (200 - b) * k
                    f = f + (1 - f) * k
                c.pen = color.rgb(int(r * f), int(g * f), int(b * f))
            c.rectangle(OX + x, OY + y, 1, 1)

    def glow(self, c, t):
        self.ambient.draw(c)
        # A soft band of light that sweeps across the year every few seconds
        bx = OX + ((t // 25) % (W + 30)) - 15
        blob(c, bx, OY + 3, 5, color.oklch(200, 200, self.hue, 40))
        self._draw_cells(c, t, True)

    def crisp(self, c, t):
        self._update_twinkles(t)
        if not self.cells:
            text(c, "No data", 8, font.sins, color.oklch(180, 200, 25))
            return
        self._draw_cells(c, t, False)


# --- Clock ------------------------------------------------------------------------------

class Clock(App):
    hue = 200
    font = font.match

    def __init__(self, data):
        super().__init__(data)
        self.ambient = Ambient(self.hue, count=5, alpha=50, hue_spread=40)
        self.shown = None
        self.prev = None
        self.changed_at = 0
        self.adv = text_width("0", self.font) + 1

    def now_str(self):
        if not self.data.time_synced:
            return None, ""
        tm = time.localtime(time.time() + int(config.TIMEZONE_OFFSET_HOURS * 3600))
        h, m, s = tm[3], tm[4], tm[5]
        suffix = ""
        if not config.CLOCK_24H:
            suffix = "AM" if h < 12 else "PM"
            h = h % 12 or 12
        return "%02d%02d" % (h, m), suffix, s

    def enter(self):
        self.shown = None

    def _layout(self, suffix):
        colon = 3
        width = self.adv * 4 + colon + (text_width(suffix, font.kobold) + 2 if suffix else 0)
        x = (W - width) // 2
        return [x, x + self.adv, x + self.adv * 2 + colon, x + self.adv * 3 + colon], x + self.adv * 2, x + self.adv * 4 + colon + 2

    def _hue(self):
        # Hue drifts slowly over the minute
        return (self.hue + int(30 * math.sin(time.ticks_ms() / 9000))) % 256

    def glow(self, c, t):
        self.ambient.draw(c)
        r = self.now_str()
        if r[0] is None:
            return
        digits, suffix, sec = r
        # A comet that crosses the display once a minute marks the seconds
        cx = OX - 3 + (sec + (time.ticks_ms() % 1000) / 1000) / 60 * (W + 6)
        blob(c, cx, OY + 6, 3, color.oklch(220, 200, (self._hue() + 60) % 256, 120))
        self._digits(c, digits, suffix, color.oklch(200, 220, self._hue(), 200), t, sec)

    def crisp(self, c, t):
        r = self.now_str()
        if r[0] is None:
            a = int(128 + 127 * math.sin(t / 250))
            text(c, "--:--", (W - text_width("--:--", self.font)) // 2, self.font, color.oklch(200, 150, self.hue, a))
            return
        digits, suffix, sec = r
        self._digits(c, digits, suffix, color.oklch(245, 90, self._hue()), t, sec)

    def _digits(self, c, digits, suffix, pen, t, sec):
        if digits != self.shown:
            self.prev = self.shown
            self.shown = digits
            self.changed_at = time.ticks_ms()
        p = min(1.0, time.ticks_diff(time.ticks_ms(), self.changed_at) / 450)
        p = easing.easeOutBack(p) if p < 1 else 1.0
        xs, colon_x, suffix_x = self._layout(suffix)
        for i, ch in enumerate(digits):
            if self.prev and self.prev[i] != ch and p < 1:
                # Old digit rolls up and out, new digit rolls up into place
                text(c, self.prev[i], xs[i], self.font, pen, -int(round(p * H)))
                text(c, ch, xs[i], self.font, pen, H - int(round(p * H)))
            else:
                text(c, ch, xs[i], self.font, pen)
        if time.ticks_ms() % 1000 < 600:
            c.pen = pen
            c.rectangle(OX + colon_x, OY + 2, 1, 1)
            c.rectangle(OX + colon_x, OY + 4, 1, 1)
        if suffix:
            text(c, suffix, suffix_x, font.kobold, pen)


# --- Counters -----------------------------------------------------------------------------

# Icons: "#" uses the app colour, other letters map to fixed colours
ICON_COLOURS = {"W": (255, 255, 255), "R": (255, 0, 0), "Y": (255, 200, 0), "O": (255, 120, 0)}

ICONS = {
    "stars": (
        "...Y...",
        "..YYY..",
        "YYYYYYY",
        ".YYYYY.",
        "..YYY..",
        ".YY.YY.",
        ".Y...Y.",
    ),
    "followers": (
        "..###..",
        "..###..",
        "..###..",
        ".......",
        ".#####.",
        "#######",
        "#######",
    ),
    "x": (
        "WW....W",
        ".WW..W.",
        "..WWW..",
        "...WW..",
        "..W.WW.",
        ".W...WW",
        "W.....W",
    ),
    "youtube": (
        ".RRRRRRR.",
        "RRRWRRRRR",
        "RRRWWRRRR",
        "RRRWWWRRR",
        "RRRWWRRRR",
        "RRRWRRRRR",
        ".RRRRRRR.",
    ),
}


class Counter(App):
    font = font.sins
    count_up_ms = 1400

    def __init__(self, data, name, hue, alpha_icon=False):
        super().__init__(data)
        self.name = name
        self.hue = hue
        self.icon = ICONS[name]
        self.icon_w = len(self.icon[0])
        self.ambient = Ambient(hue, count=5, alpha=45)
        self.sparkles = Sparkles(hue)
        self.last_value = None

    def enter(self):
        v = self.data.get(self.name)
        self.from_value = 0
        if v and self.last_value is not None and v[0] > self.last_value:
            # Grew since we last showed it: count up from the old value and celebrate
            self.from_value = self.last_value
            self.sparkles.burst(12, self.icon_w + 2, W)
        self.last_value = v[0] if v else None

    def _text(self, t):
        v = self.data.get(self.name)
        if not v:
            return None
        target, approx = v
        p = easing.easeOutCubic(min(1.0, t / self.count_up_ms))
        n = int(self.from_value + (target - self.from_value) * p)
        return format_count(n, approx, W - self.icon_w - 2, self.font)

    def _icon(self, c, t, glow):
        # The icon pops in with a little bounce
        p = easing.easeOutBack(min(1.0, t / 500))
        y_off = int(round((1 - p) * -H))
        for y, row in enumerate(self.icon):
            for x, ch in enumerate(row):
                if ch == ".":
                    continue
                rgb = ICON_COLOURS.get(ch)
                c.pen = color.rgb(*rgb) if rgb else color.oklch(235, 120, self.hue)
                c.rectangle(OX + x, OY + y + y_off, 1, 1)

    def _layout(self, s):
        tw = text_width(s, self.font)
        avail = W - self.icon_w - 2
        return self.icon_w + 2 + max(0, (avail - tw) // 2)

    def glow(self, c, t):
        self.ambient.draw(c)
        # Icon breathes softly
        a = int(120 + 80 * math.sin(t / 600))
        blob(c, OX + self.icon_w / 2, OY + 3, 4, color.oklch(200, 200, self.hue, a))
        s = self._text(t)
        if s:
            text(c, s, self._layout(s), self.font, color.oklch(200, 230, self.hue, 220))

    def crisp(self, c, t):
        self._icon(c, t, False)
        s = self._text(t)
        if s is None:
            a = int(128 + 127 * math.sin(t / 250))
            text(c, "...", self.icon_w + 8, self.font, color.oklch(220, 100, self.hue, a))
        else:
            text(c, s, self._layout(s), self.font, color.oklch(250, 95, self.hue))
        self.sparkles.update(self.icon_w + 2, W)
        self.sparkles.draw(c)


def build(names, data):
    makers = {
        "graph": lambda: Graph(data),
        "clock": lambda: Clock(data),
        "stars": lambda: Counter(data, "stars", 75),
        "followers": lambda: Counter(data, "followers", 210),
        "x": lambda: Counter(data, "x", 180),
        "youtube": lambda: Counter(data, "youtube", 20),
    }
    return [(n, makers[n]()) for n in names if n in makers]
