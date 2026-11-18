# The snappy menu (from the widget's original firmware) and its sub-screens.
# Frame driven: the host calls Menu.frame() each frame with new input events,
# nothing here blocks, so audio, config flushing and the watchdog keep going.

import random

from gitmatrix import _input
from gitmatrix._compat import ticks_diff
from gitmatrix._display import HEIGHT, WIDTH, ease_in_quart

SNAP_THRESHOLD = 1      # turns needed to snap to the next item
SNAP_TIMEOUT_MS = 800
SNAP_SPEED = 1.5        # how snappily the next item slides in
PEEK_AMOUNT = 0.45      # how far the next item peeks in (eased)
GLOW = True
IDLE_MS = 30000         # close the menu after this long without input
FRAME_MS = 1000 / 60    # the original tuned its physics per 60 fps frame

PAD_X = 10
PAD_Y = 7

MAIN_ITEMS = ("Plugins", "Brightness", "Volume", "Alerts", "WiFi", "Bluetooth", "< Back")


def _font():
    return font.sins


def text_size(target, text):
    target.font = _font()
    return target.measure_text(text)


def draw_text(target, text, x=None, pen=None):
    tw, th = text_size(target, text)
    if x is None:
        x = (target.width - tw) // 2
    target.pen = pen if pen is not None else color.rgb(255, 255, 255)
    target.text(text, vec2(x, (target.height - th) // 2))
    return tw


def draw_marquee(target, text, elapsed_ms, pen=None):
    """Centre text that fits, else scroll it after a short pause."""
    tw, _ = text_size(target, text)
    if tw <= target.width:
        draw_text(target, text, pen=pen)
        return
    gap = 15
    x = 1 - (max(0, elapsed_ms - 700) // 40) % (tw + gap)
    draw_text(target, text, x, pen)
    draw_text(target, text, x + tw + gap, pen)


def blank(target):
    target.pen = color.rgb(0, 0, 0)
    target.clear()


class _Ball:
    def __init__(self, w, h):
        self.r = random.uniform(3, 7)
        self.x = random.uniform(-self.r, w - self.r)
        self.y = random.uniform(-self.r, h - self.r)
        self.dx = (random.uniform(-1, 1) + 0.01) / 10
        self.dy = (random.uniform(-1, 1) + 0.01) / 10

    def update(self, w, h, frames):
        self.x += self.dx * frames
        self.y += self.dy * frames
        if self.x < 0:
            self.x, self.dx = 0, -self.dx
        if self.x > w:
            self.x, self.dx = w, -self.dx
        if self.y < 0:
            self.y, self.dy = 0, -self.dy
        if self.y > h - 1:
            self.y, self.dy = h - 1, -self.dy


class Snappy:
    """Items slide across; a turn peeks the next one in, then it snaps over."""

    def __init__(self, items, hue_step=25):
        self.items = list(items)
        self.n = len(self.items)
        self.canvas = image(WIDTH + 2 * PAD_X, HEIGHT * 3)
        self.hues = [i * hue_step for i in range(self.n)]
        # pens built once: allocating in the frame loop stalls the BT audio path
        self.ball_pens = [color.oklch(200, 100, h, 25) for h in self.hues]
        self.glow_pens = [color.oklch(255, 100, h, 150) for h in self.hues]
        self.text_pens = [color.oklch(255, 70, h) for h in self.hues]
        self.black = color.rgb(0, 0, 0)
        self.balls = [_Ball(self.canvas.width, self.canvas.height) for _ in range(10)]
        self.circle = shape.circle(0, 0, 1.0)
        self.widths = [text_size(self.canvas, t) for t in self.items]
        self.reset()

    def reset(self, index=0):
        self.value = 0.0
        self.offset = 0.0
        self.app_offset = (self.n - index + 1) % self.n   # snaps to `index` on the first step
        self.direction = -1
        self.current = None
        self.time_changed = None
        self.turned = None

    def turn(self, delta, now):
        if delta:
            self.value += 1
            self.direction = 1 if delta > 0 else -1
            self.time_changed = now

    def step(self, now, frames=1.0):
        speed = 0.01 * SNAP_SPEED * frames
        if self.value >= SNAP_THRESHOLD:
            self.offset += speed
        elif self.value > 0:
            self.offset = self.value * PEEK_AMOUNT

        if self.offset > 1.0 or self.current is None:
            self.offset = 0.0
            self.value = 0.0
            self.app_offset = (self.app_offset + self.direction) % self.n
            self.current = (self.n - self.app_offset) % self.n

        if self.time_changed is not None and self.value < SNAP_THRESHOLD:
            if ticks_diff(now, self.time_changed) > SNAP_TIMEOUT_MS:
                self.value -= speed
                if self.value < 0.01:
                    self.value = 0.0
                    self.time_changed = None

    def animating(self):
        return self.value > 0 or self.offset > 0

    def selected(self):
        """The item a press picks: the one snapping in if the knob has committed to it."""
        if self.value >= SNAP_THRESHOLD:
            return (self.n - (self.app_offset + self.direction)) % self.n
        return self.current

    def _x(self, i, prog):
        x = (WIDTH - WIDTH * prog) % (WIDTH * self.n) - (WIDTH - PAD_X)
        return x + (WIDTH - self.widths[i][0]) // 2

    def draw(self, target, frames=1.0):
        c = self.canvas
        c.pen = self.black
        c.clear()
        c.font = _font()
        eased = ease_in_quart(self.offset % 1) * self.direction
        if GLOW:
            c.pen = self.ball_pens[self.current or 0]
            for b in self.balls:
                b.update(c.width, c.height, frames)
                self.circle.transform = mat3().translate(b.x, b.y).scale(b.r)
                c.shape(self.circle)
            for i, t in enumerate(self.items):
                c.pen = self.glow_pens[i]
                c.text(t, vec2(self._x(i, eased + i + self.app_offset), (c.height - self.widths[i][1]) // 2))
            c.blur(2)
        for i, t in enumerate(self.items):
            c.pen = self.text_pens[i]
            c.text(t, vec2(self._x(i, eased + i + self.app_offset), (c.height - self.widths[i][1]) // 2))
        target.blit(c, vec2(-PAD_X, -PAD_Y))


# ---- screens --------------------------------------------------------------------
# A screen gets turn()/press() and draw(); returning True from press() pops it.


class SnappyScreen:
    def __init__(self, items, on_select, start=0):
        self.snappy = Snappy(items)
        self.snappy.reset(start)
        self.on_select = on_select

    def turn(self, d, now):
        self.snappy.turn(d, now)

    def press(self, now):  # noqa: ARG002
        return self.on_select(self.snappy.items[self.snappy.selected()])

    def draw(self, target, now, frames):
        self.snappy.step(now, frames)
        self.snappy.draw(target, frames)


class ListScreen:
    """A marquee list with a position bar, like the original Apps screen."""

    def __init__(self, labels, on_select, start=0, hues=None):
        self.labels = labels
        self.on_select = on_select
        self.idx = start % len(labels)
        self.hues = hues
        self.shown = None

    def turn(self, d, now):
        self.idx = (self.idx + (1 if d > 0 else -1)) % len(self.labels)
        self.shown = now

    def press(self, now):  # noqa: ARG002
        return self.on_select(self.idx)

    def draw(self, target, now, frames):  # noqa: ARG002
        if self.shown is None:
            self.shown = now
        blank(target)
        hue = self.hues[self.idx] if self.hues else self.idx * 40
        draw_marquee(target, self.labels[self.idx], ticks_diff(now, self.shown), color.oklch(220, 150, hue))
        n = len(self.labels)
        target.pen = color.rgb(60, 60, 60)
        target.rectangle(int(self.idx * WIDTH / n), HEIGHT - 1, max(1, WIDTH // n), 1)


class SliderScreen:
    def __init__(self, get, set_, step=0.05, minimum=0.0, hue=140):
        self.get = get
        self.set = set_
        self.step = step
        self.minimum = minimum
        self.hue = hue
        self.level = get()

    def turn(self, d, now):  # noqa: ARG002
        self.level = min(1.0, max(self.minimum, self.level + (self.step if d > 0 else -self.step)))
        self.set(self.level, False)

    def press(self, now):  # noqa: ARG002
        self.set(self.level, True)
        return True

    def draw(self, target, now, frames):  # noqa: ARG002
        blank(target)
        target.pen = color.oklch(120, 150, self.hue)
        target.rectangle(0, 0, int(WIDTH * self.level + 0.5), HEIGHT)
        draw_text(target, f"{int(self.level * 100 + 0.5)}%")


class MessageScreen:
    def __init__(self, text, hue=240):
        self.text = text if callable(text) else (lambda: text)
        self.hue = hue
        self.since = None

    def turn(self, d, now):
        pass

    def press(self, now):  # noqa: ARG002
        return True

    def draw(self, target, now, frames):  # noqa: ARG002
        if self.since is None:
            self.since = now
        blank(target)
        draw_marquee(target, self.text(), ticks_diff(now, self.since), color.oklch(200, 150, self.hue))


class Menu:
    """The menu stack. `api` is the host (see Host.menu_* methods)."""

    def __init__(self, api):
        self.api = api
        self.stack = []
        self.open_since = 0
        self.last_input = 0
        self.last_frame = None

    @property
    def is_open(self):
        return bool(self.stack)

    def open(self, now):
        self.stack = [SnappyScreen(MAIN_ITEMS, self._main_select)]
        self.open_since = now
        self.last_input = now
        self.last_frame = None

    def close(self):
        self.stack = []

    def push(self, screen_):
        self.stack.append(screen_)

    def frame(self, target, events, now):
        """Handle input then draw. Returns False once the menu has closed."""
        for e in events:
            if not self.stack:
                break
            if e.kind == _input.TURN:
                self.last_input = now
                self.stack[-1].turn(e.value, now)
            elif e.kind == _input.PRESS:
                self.last_input = now
                if self.stack[-1].press(now):
                    self.stack.pop()
                    if not self.stack:
                        self.api.menu_closed()
        if self.stack and ticks_diff(now, self.last_input) > IDLE_MS:
            self.close()
            self.api.menu_closed()
        if not self.stack:
            return False
        frames = 1.0 if self.last_frame is None else min(4.0, (ticks_diff(now, self.last_frame)) / FRAME_MS)
        self.last_frame = now
        self.stack[-1].draw(target, now, frames)
        return True

    # ---- items -------------------------------------------------------------------

    def _main_select(self, item):
        api = self.api
        if item == "< Back":
            return True
        if item == "Plugins":
            self.push(self._plugins_screen())
        elif item == "Brightness":
            self.push(SliderScreen(api.menu_get_brightness, api.menu_set_brightness, minimum=0.05))
        elif item == "Volume":
            self.push(SliderScreen(api.menu_get_volume, api.menu_set_volume, step=1 / 16, hue=300))
        elif item == "Alerts":
            self.push(self._alerts_screen())
        elif item == "WiFi":
            self.push(MessageScreen(api.menu_wifi_status, 240))
        elif item == "Bluetooth":
            self.push(self._bluetooth_screen())
        return False

    def _plugins_screen(self):
        entries = self.api.menu_plugins()   # [(name, label, quarantined)]
        labels = [("! " + label) if bad else label for _, label, bad in entries] + ["< Back"]
        current = self.api.menu_current_plugin()
        names = [e[0] for e in entries]
        start = names.index(current) if current in names else 0

        def select(idx):
            if idx >= len(entries):
                return True
            self.api.menu_show_plugin(entries[idx][0])
            self.close()
            self.api.menu_closed()
            return False

        return ListScreen(labels, select, start)

    def _alerts_screen(self):
        api = self.api

        def label():
            sound = "Sound on" if api.config.get("alert_sound", True) else "Sound off"
            quiet = "Quiet hrs on" if api.config.get("quiet_hours_enabled", False) else "Quiet hrs off"
            return sound, quiet

        def select(item):
            if item.startswith("Sound"):
                api.config.set("alert_sound", not api.config.get("alert_sound", True))
            elif item.startswith("Quiet"):
                api.config.set("quiet_hours_enabled", not api.config.get("quiet_hours_enabled", False))
            else:
                return True
            # rebuild so the labels show the new state
            self.stack[-1] = self._alerts_screen()
            return False

        return SnappyScreen((*label(), "< Back"), select)

    def _bluetooth_screen(self, start=0):
        api = self.api
        pairable = "Pairable on" if api.menu_get_pairable() else "Pairable off"

        def select(item):
            if item == "Status":
                self.push(MessageScreen(api.menu_bt_status, 200))
            elif item == "Forget all":
                api.menu_forget_bt()
                self.push(MessageScreen("Forgotten - pair again", 25))
            elif item.startswith("Pairable"):
                api.menu_set_pairable(not api.menu_get_pairable())
                # rebuild so the label shows the new state
                self.stack[-1] = self._bluetooth_screen(2)
            else:
                return True
            return False

        return SnappyScreen(("Status", "Forget all", pairable, "< Back"), select, start)
