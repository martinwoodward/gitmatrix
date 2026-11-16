import gc
import os
import json
import time
import machine
import easing
import contribution_widget  # noqa: F401
from encoder import Encoder
import random

SETTINGS_FILE = "/settings.json"
LAUNCH_FILE = "/launch.txt"
BUTTON_PIN = 20  # board.SWITCH, active low
APP_EXCLUDE = ("main.py", "boot.py", "secrets.py", "secrets.example.py")


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


def launch_pending_app():
    # Apps are started from a fresh boot so they get clean PIO/RAM. Pressing the
    # button while an app runs resets back to this menu.
    try:
        with open(LAUNCH_FILE) as f:
            app = f.read().strip()
        os.remove(LAUNCH_FILE)
    except OSError:
        return

    def arm(_):
        button.irq(trigger=machine.Pin.IRQ_FALLING, handler=lambda p: machine.reset())

    # Delay arming so the press that launched the app doesn't immediately exit it
    global _arm_timer
    _arm_timer = machine.Timer(mode=machine.Timer.ONE_SHOT, period=1000, callback=arm)
    print("Launching", app)
    try:
        with open(app) as f:
            exec(f.read(), {"__name__": "__main__"})
    except Exception as e:  # noqa: BLE001
        print("App error:", e)
    machine.reset()


launch_pending_app()


SNAP_THRESHOLD = 1  # Number of turns required to snap to the next item, for > 1 turns the timeout below applies
SNAP_TIMEOUT_MS = 800
SNAP_SPEED = 1.5 # Multiplier for how snappily the next item should slide in
PEEK_AMOUNT = 0.45 # How far the next item shoud peek in, this value is eased so it's unintuitive
FONT = rom_font.sins
GLOW = True  # glow around words
CENTER_TEXT = True

width = 53
height = 7

"""
A demonstration of a rotary encoder being used to control a value.
"""

# Free up hardware resources ahead of creating a new Encoder
gc.collect()

# Create an encoder on the 3 ADC pins, using PIO 0 and State Machine 0
PIN_A = 22#Pin.board.ENC_A    # The A channel pin
PIN_B = 21#Pin.board.ENC_B    # The B channel pin
enc = Encoder(1, 0, (PIN_A, PIN_B))

value = 0

# Print out the initial value
print("Value =", value)

modes = [
    "Brightn...",
    "Volume",
    "WiFi",
    "Apps",
    "Update",
    "Test"
]


def mode_scroll(text, font_face=None, fg=None, target=None):
    font_face = font_face or rom_font.sins
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
        circle.transform = mat3().translate(ball.x, ball.y).scale(ball.r)
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


def apps_screen():
    apps = sorted(f for f in os.listdir("/") if f.endswith(".py") and f not in APP_EXCLUDE)
    apps.append("< Back")
    idx = 0
    shown = time.ticks_ms()
    enc.delta()
    while True:
        d = enc.delta()
        if d:
            idx = (idx + (1 if d > 0 else -1)) % len(apps)
            shown = time.ticks_ms()
        if btn.pressed():
            if apps[idx] == "< Back":
                return
            with open(LAUNCH_FILE, "w") as f:
                f.write(apps[idx])
            machine.reset()

        blank()
        name = apps[idx][:-3] if apps[idx].endswith(".py") else apps[idx]
        pen = color.oklch(220, 150, idx * 40)
        screen.font = FONT
        tw, _ = screen.measure_text(name)
        if tw <= width:
            draw_text(name, pen=pen)
        else:
            # Marquee long names after a short pause
            gap = 15
            elapsed = time.ticks_diff(time.ticks_ms(), shown) - 700
            x = 1 - (max(0, elapsed) // 40) % (tw + gap)
            draw_text(name, x, pen)
            draw_text(name, x + tw + gap, pen)
        # Position indicator along the bottom row
        screen.pen = color.rgb(60, 60, 60)
        screen.rectangle(int(idx * width / len(apps)), height - 1, max(1, width // len(apps)), 1)
        display.update()


def coming_soon():
    blank()
    draw_text("Soon!", pen=color.oklch(200, 150, 60))
    display.update()
    time.sleep_ms(800)


def open_mode(name):
    if name.startswith("Brightn"):
        brightness_screen()
    elif name == "Apps":
        apps_screen()
    else:
        coming_soon()
    enc.delta()


current_mode = 0
next_mode = 1

last_value = 0

offset = 0

app_offset = 0

time_changed = None
current_app = None

direction = 1

# Loop forever
while True:
    if current_app is not None and btn.pressed():
        open_mode(modes[current_app][0])
        continue

    canvas.pen = color.rgb(0, 0, 0)
    canvas.clear()

    delta = enc.delta()
    last_value = value
    if delta != 0:
        value += 1
        direction = 1 if delta > 0 else -1

    #value = math.sin(time.ticks_ms() / 1000) + 1 / 2.0

    # Creep the next app in from the right and snap across at a threshold
    if value >= SNAP_THRESHOLD:
        offset += 0.01 * SNAP_SPEED
    elif value > 0:
        offset = value * PEEK_AMOUNT

    # Snap to the selected app
    if offset > 1.0 or current_app is None:
        offset = 0
        value = 0
        app_offset += direction
        app_offset %= num_modes

        # Need to invert the offset to get the currently selected app
        current_app = (num_modes - app_offset) % num_modes

        print(modes[current_app][0])


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
