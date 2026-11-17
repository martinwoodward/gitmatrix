import gc
import json
import time
import machine
import easing
import contribution_widget  # noqa: F401
from encoder import Encoder
import random

import config
from datasource import Data
import widgets

SETTINGS_FILE = "/settings.json"
BUTTON_PIN = 20  # board.SWITCH, active low


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f)


settings = load_settings()
display.set_brightness(settings.get("brightness", 1.0))

button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)

SNAP_THRESHOLD = 1  # Number of turns required to snap to the next item, for > 1 turns the timeout below applies
SNAP_TIMEOUT_MS = 800
SNAP_SPEED = 1.5  # Multiplier for how snappily the next item should slide in
PEEK_AMOUNT = 0.45  # How far the next item shoud peek in, this value is eased so it's unintuitive
FONT = font.sins
GLOW = True  # glow around words
CENTER_TEXT = True
MENU_IDLE_MS = 30 * 1000  # Return to the carousel after this long without input
TRANSITION_MS = 700

width = 53
height = 7

gc.collect()
enc = Encoder(1, 0, (22, 21))

modes = [
    "Apps",
    "Brightn...",
    "WiFi",
    "Update",
    "Volume",
    "Test",
    "< Back",
]

def mode_scroll(text, font_face=None, fg=None, target=None):
    font_face = font_face or font.sins
    fg = fg or color.white

    target = target or screen
    target.font = font_face

    tw, th = target.measure_text(text)

    scroll_distance = 53

    offset_y = (target.height - th) // 2

    offset = vec2(0, offset_y)

    def update(progress=0):
        target.pen = fg
        offset.x = 53 - (scroll_distance * progress)
        offset.x %= 53 * num_modes
        offset.x -= 43

        if CENTER_TEXT:
            offset.x += (53 - tw) // 2
        else:
            offset.x -= 1  # Fudge to align to the left

        target.font = font_face


        target.text(text, offset)

    return update


canvas = image(53 + 20, 7 * 3)
canvas2 = image(53 + 20, 7 * 3)

num_modes = len(modes)

modes = [
    (
    mode,
    mode_scroll(mode, font_face=FONT, fg=color.oklch(255, 100, i * 25, 150), target=canvas),
    mode_scroll(mode, font_face=FONT, fg=color.oklch(255, 70, i * 25), target=canvas),
    color.oklch(200, 100, i * 25, 25)
    )
    for i, mode in enumerate(modes)]

circle = shape.circle(0, 0, 1.0)

class Ball:
    def __init__(self):
        self.r = random.uniform(3, 7)
        self.x = random.uniform(-self.r, width - self.r)
        self.y = random.uniform(-self.r, height - self.r)
        self.dx = (random.uniform(-1, 1) + 0.01) / 10
        self.dy = (random.uniform(-1, 1) + 0.01) / 10

    def update(self, target):
        self.x += self.dx
        self.y += self.dy

        max_x = target.width

        if not CENTER_TEXT:
            max_x -= 20

        if self.x < 0:
            self.x = 0
            self.dx *= -1

        if self.x > max_x:
            self.x = max_x
            self.dx *= -1

        if self.y < 0:
            self.y = 0
            self.dy *= -1

        if self.y > target.height - 1:
            self.y = target.height - 1
            self.dy *= -1

    def draw(self, target, pen):
        target.pen = pen
        circle.transform = mat3().translate(self.x, self.y).scale(self.r)
        target.shape(circle)



balls = [Ball() for _ in range(10)]


class Button:
    def __init__(self, pin):
        self.pin = pin
        self.last = pin.value()
        self.changed = 0

    def pressed(self):
        v = self.pin.value()
        now = time.ticks_ms()
        if v != self.last and time.ticks_diff(now, self.changed) > 30:
            self.last = v
            self.changed = now
            return v == 0
        return False


btn = Button(button)


def draw_text(text, x=None, pen=None):
    screen.font = FONT
    tw, th = screen.measure_text(text)
    if x is None:
        x = (width - tw) // 2
    screen.pen = pen or color.white
    screen.text(text, vec2(x, (height - th) // 2))
    return tw


def blank():
    screen.pen = color.rgb(0, 0, 0)
    screen.clear()


def draw_marquee(text, since, pen=None):
    # Centre text that fits; otherwise scroll it after a short pause
    screen.font = FONT
    tw, _ = screen.measure_text(text)
    if tw <= width:
        draw_text(text, pen=pen)
        return
    gap = 15
    elapsed = time.ticks_diff(time.ticks_ms(), since) - 700
    x = 1 - (max(0, elapsed) // 40) % (tw + gap)
    draw_text(text, x, pen)
    draw_text(text, x + tw + gap, pen)


def message(text, pen=None):
    blank()
    draw_text(text, pen=pen)
    display.update()


def wait_for_press(text, pen=None):
    since = time.ticks_ms()
    while not btn.pressed():
        blank()
        draw_marquee(text, since, pen)
        display.update()


RED = color.oklch(180, 200, 25)
GREEN = color.oklch(200, 180, 140)


def wifi_connect():
    import network
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        return wlan
    try:
        import secrets  # noqa: F401
    except ImportError:
        return None
    import ezwifi
    message("WiFi...", color.oklch(200, 150, 240))
    return wlan if ezwifi.connect() else None


def wifi_screen():
    try:
        import secrets
        ssid = secrets.WIFI_SSID
    except (ImportError, AttributeError):
        wait_for_press("No secrets.py - see README", RED)
        return
    wlan = wifi_connect()
    if wlan:
        wait_for_press(f"{ssid} {wlan.ifconfig()[0]} {wlan.status('rssi')}dBm", GREEN)
    else:
        wait_for_press(f"Can't join {ssid}", RED)


def brightness_screen():
    level = display.get_brightness()
    enc.delta()
    while not btn.pressed():
        d = enc.delta()
        if d:
            level = min(1.0, max(0.05, level + (0.05 if d > 0 else -0.05)))
            display.set_brightness(level)
        blank()
        screen.pen = color.oklch(120, 150, 140)
        screen.rectangle(0, 0, int(width * level), height)
        draw_text(f"{int(level * 100 + 0.5)}%")
        display.update()
    settings["brightness"] = level
    save_settings(settings)



data = Data(config.APPS)
apps = widgets.build(config.APPS, data)
app_index = 0


def update_screen():
    data.refresh_all()
    message("Updating", color.oklch(200, 150, 240))
    time.sleep_ms(700)
    return True  # Back to the carousel to watch the values arrive


def apps_screen():
    global app_index
    names = [n.capitalize() if n != "x" else "X" for n, _ in apps] + ["< Back"]
    idx = app_index
    shown = time.ticks_ms()
    enc.delta()
    while True:
        data.poll()
        d = enc.delta()
        if d:
            idx = (idx + (1 if d > 0 else -1)) % len(names)
            shown = time.ticks_ms()
        if btn.pressed():
            if idx == len(apps):
                return False
            app_index = idx
            return True
        blank()
        draw_marquee(names[idx], shown, color.oklch(220, 150, idx * 40))
        # Position indicator along the bottom row
        screen.pen = color.rgb(60, 60, 60)
        screen.rectangle(int(idx * width / len(names)), height - 1, max(1, width // len(names)), 1)
        display.update()


def coming_soon():
    blank()
    draw_text("Soon!", pen=color.oklch(200, 150, 60))
    display.update()
    time.sleep_ms(800)


def open_mode(name):
    # Returns True to leave the menu and go back to the carousel
    if name == "Apps":
        return apps_screen()
    if name.startswith("Brightn"):
        brightness_screen()
    elif name == "WiFi":
        wifi_screen()
    elif name == "Update":
        return update_screen()
    elif name == "< Back":
        return True
    else:
        coming_soon()
    return False


def slide(old, new, p, direction):
    # Compose two app canvases sliding horizontally; p is 0..1
    s = int(round(easing.easeInOutCubic(p) * width))
    ox, oy = widgets.OX, widgets.OY
    if direction < 0:
        old, new = new, old
        s = width - s
    if s < width:
        screen.blit(old, rect(ox + s, oy, width - s, height), rect(0, 0, width - s, height))
    if s > 0:
        screen.blit(new, rect(ox, oy, s, height), rect(width - s, 0, s, height))


def carousel():
    global app_index
    name, app = apps[app_index]
    app.enter()
    entered = time.ticks_ms()
    prev = None  # (app, entered) while a transition is running
    direction = 1
    enc.delta()
    while True:
        now = time.ticks_ms()
        if prev is None:
            # Skip network work mid-transition so the TLS handshake can't stutter it
            data.poll()
        if btn.pressed():
            return
        d = enc.delta()
        step = 0
        if d:
            step = 1 if d > 0 else -1
        elif prev is None and len(apps) > 1 and time.ticks_diff(now, entered) > config.APP_INTERVAL_S * 1000:
            step = 1
        if step and prev is None:
            prev = (app, entered, now)
            direction = step
            app_index = (app_index + step) % len(apps)
            name, app = apps[app_index]
            app.enter()
            entered = now
            print("app:", name)

        app.render(canvas, time.ticks_diff(now, entered))
        if prev is not None:
            p = time.ticks_diff(now, prev[2]) / TRANSITION_MS
            if p >= 1:
                prev = None
            else:
                prev[0].render(canvas2, time.ticks_diff(now, prev[1]))
                slide(canvas2, canvas, p, direction)
        if prev is None:
            screen.blit(canvas, vec2(-widgets.OX, -widgets.OY))
        display.update()


def menu():
    global value, last_value, offset, app_offset, time_changed, current_app, direction
    idle_since = time.ticks_ms()
    enc.delta()
    while True:
        data.poll()
        if current_app is not None and btn.pressed():
            if open_mode(modes[current_app][0]):
                return
            enc.delta()
            idle_since = time.ticks_ms()
            continue

        if time.ticks_diff(time.ticks_ms(), idle_since) > MENU_IDLE_MS:
            return

        canvas.pen = color.rgb(0, 0, 0)
        canvas.clear()

        delta = enc.delta()
        last_value = value
        if delta != 0:
            value += 1
            direction = 1 if delta > 0 else -1
            idle_since = time.ticks_ms()

        # Creep the next item in from the right and snap across at a threshold
        if value >= SNAP_THRESHOLD:
            offset += 0.01 * SNAP_SPEED
        elif value > 0:
            offset = value * PEEK_AMOUNT

        # Snap to the selected item
        if offset > 1.0 or current_app is None:
            offset = 0
            value = 0
            app_offset += direction
            app_offset %= num_modes

            # Need to invert the offset to get the currently selected item
            current_app = (num_modes - app_offset) % num_modes

        if last_value != value:
            time_changed = time.ticks_ms()

        if time_changed:
            if time.ticks_diff(time.ticks_ms(), time_changed) > SNAP_TIMEOUT_MS and value < SNAP_THRESHOLD:
                value -= 0.01 * SNAP_SPEED
                last_value = value
                if value < 0.01:
                    value = 0
                    time_changed = None

        if GLOW:
            for ball in balls:
                ball.update(canvas)
                ball.draw(canvas, modes[current_app][3])

            for i in range(num_modes):
                prog = easing.easeInQuart(offset % 1) * direction + i + app_offset
                modes[i][1](prog)

            canvas.blur(2)

        for i in range(num_modes):
            prog = easing.easeInQuart(offset % 1) * direction + i + app_offset
            modes[i][2](prog)

        screen.blit(canvas, vec2(-10, -7))

        display.update()


value = 0
last_value = 0
offset = 0
app_offset = 1  # Snaps to 0 on the first frame so the menu opens on the first item
time_changed = None
current_app = None
direction = -1

while True:
    carousel()
    # Reopen the menu on its first item each time
    app_offset, direction, current_app, value, offset, time_changed = 1, -1, None, 0, 0, None
    menu()
