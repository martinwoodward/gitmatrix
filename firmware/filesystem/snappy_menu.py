import gc
import time
import easing
import contribution_widget  # noqa: F401
from encoder import Encoder
import random


SNAP_THRESHOLD = 1  # Number of turns required to snap to the next item, for > 1 turns the timeout below applies
SNAP_TIMEOUT_MS = 800
SNAP_SPEED = 1.5 # Multiplier for how snappily the next item should slide in
PEEK_AMOUNT = 0.45 # How far the next item shoud peek in, this value is eased so it's unintuitive
FONT = font.sins
GLOW = False  # glow around words
BALLS = True # bouncy blurry balls
BLUR = 3
CENTER_TEXT = True
REDUCE_FLASHING = True # reduce brightness during transitions
TEXT_BRIGHTNESS = 0.8

display.set_brightness(1)

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
    "Screen",
    "Volume",
    "WiFi",
    "Apps",
    "Update",
    "Test"
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

num_modes = len(modes)

modes = [
    (
    mode,
    mode_scroll(mode, font_face=FONT, fg=color.oklch(255, 100, i * 25, 150), target=canvas),
    mode_scroll(mode, font_face=FONT, fg=color.oklch(255, 70, i * 25, 255 * TEXT_BRIGHTNESS), target=canvas),
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
    canvas.pen = color.rgb(0, 0, 0, 255)
    canvas.clear()

    delta = enc.delta()
    last_value = value
    if delta != 0:
        value += 1
        direction = 1 if delta > 0 else -1

    #value = math.sin(time.ticks_ms() / 1000) + 1 / 2.0

    # Creep the next app in from the right and snap across at a threshold
    if value >= SNAP_THRESHOLD:
        offset += 0.01 * SNAP_SPEED * value
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

    if time_changed and SNAP_THRESHOLD > 1:
        if time.ticks_diff(time.ticks_ms(), time_changed) > SNAP_TIMEOUT_MS and value < SNAP_THRESHOLD:
            value -= 0.01 * SNAP_SPEED
            last_value = value
            if value < 0.01:
                value = 0
                time_changed = None

    if REDUCE_FLASHING:
        if value > 0.5:
            if canvas.alpha > 32:
                canvas.alpha //= 2
        else:
            if canvas.alpha < 255:
                canvas.alpha += 2

    if BALLS:
        for ball in balls:
            ball.update(canvas)
            ball.draw(canvas, modes[current_app][3])

    if GLOW:
        for i in range(num_modes):
            prog = easing.easeOutQuad(offset) * direction + i + app_offset
            modes[i][1](prog)

    if BLUR and (GLOW or BALLS):
        canvas.blur(BLUR)

    for i in range(num_modes):
        prog = easing.easeOutQuart(offset % 1) * direction + i + app_offset
        modes[i][2](prog)

    screen.blit(canvas, vec2(-10, -7))

    display.update()
