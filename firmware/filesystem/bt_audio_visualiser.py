import gc
import math
import time
import array
import contribution_widget  # noqa: F401
from encoder import Encoder
from machine import Pin
import bt_audio

"""
Bluetooth A2DP visualiser, Winamp style. Turn the knob to cycle the modes;
hold the switch and turn it for volume.

Pair with "Widget" and play something. core1 publishes 512 FFT magnitude bins
every ~23ms; this loop folds them into one level per column and hands them to
the current mode, which paints them with PicoVector brushes: pattern brushes
for segmented bars, scrolling flames and two tone traces, a gradient for the
analyser ramp, a fractal for plasma.

The draw loop allocates nothing. Every brush, image and buffer is built up
front, because a GC pass here stalls the BTstack run loop and the audio path
hears it as an underrun.
"""

WIDTH = screen.width
HEIGHT = screen.height

FIRST_BIN = 2
LAST_BIN = 240

MIN_SCALE = 300
GAIN_ATTACK = 0.2
GAIN_DECAY = 0.997

ATTACK = 0.45
GRAVITY = 0.06
PEAK_HOLD_GRAVITY = 0.012
ENV_ATTACK = 0.4
ENV_RELEASE = 0.08

KEYS_FILE = "bt_link_keys.bin"

LABEL_MS = 700

ENC_PIN_A = 22
ENC_PIN_B = 21
SWITCH_PIN = 20
VOLUME_STEP = 4

gc.collect()
enc = Encoder(1, 0, (ENC_PIN_A, ENC_PIN_B))
switch = Pin(SWITCH_PIN, Pin.IN, Pin.PULL_UP)

CLEAR = color.rgb(0, 0, 0, 0)


def solid(c):
    """A colour wrapped as a brush once, so setting the pen never allocates."""
    return brush.pattern(c, c, 0)


BLACK = solid(color.rgb(0, 0, 0))
WHITE = solid(color.rgb(255, 255, 255))

bins = array.array("H", bytearray(512 * 2))
targets = array.array("f", bytearray(WIDTH * 4))
levels = array.array("f", bytearray(WIDTH * 4))
falls = array.array("f", bytearray(WIDTH * 4))
peaks = array.array("f", bytearray(WIDTH * 4))
peak_falls = array.array("f", bytearray(WIDTH * 4))
scale = MIN_SCALE
bass = 0.0
energy = 0.0

bin_edges = array.array("H", bytearray((WIDTH + 1) * 2))
for x in range(WIDTH + 1):
    t = x / WIDTH
    bin_edges[x] = FIRST_BIN + int((LAST_BIN - FIRST_BIN) * (t * t))
for x in range(WIDTH):
    if bin_edges[x + 1] <= bin_edges[x]:
        bin_edges[x + 1] = bin_edges[x] + 1


def follow(current, target, attack, release):
    """An envelope follower: quick on the way up, easy on the way down."""
    if target > current:
        return current + (target - current) * attack
    return current + (target - current) * release


def analyse():
    """Fold the bins into smoothed per column levels, then the bass and energy envelopes."""
    global scale, bass, energy

    frame_peak = 0
    for x in range(WIDTH):
        peak = 0
        for b in range(bin_edges[x], bin_edges[x + 1]):
            v = bins[b]
            if v > peak:
                peak = v
        if peak > frame_peak:
            frame_peak = peak
        targets[x] = peak

    if frame_peak > scale:
        scale += (frame_peak - scale) * GAIN_ATTACK
    else:
        scale = max(MIN_SCALE, scale * GAIN_DECAY)

    total = 0.0
    prev = targets[0]
    for x in range(WIDTH):
        cur = targets[x]
        nxt = targets[x + 1] if x + 1 < WIDTH else cur
        target = (prev + cur + cur + nxt) * HEIGHT / (4 * scale)
        prev = cur
        if target > HEIGHT:
            target = HEIGHT

        if target > levels[x]:
            levels[x] += (target - levels[x]) * ATTACK
            falls[x] = 0.0
        else:
            falls[x] += GRAVITY
            levels[x] = max(target, levels[x] - falls[x])

        if levels[x] >= peaks[x]:
            peaks[x] = levels[x]
            peak_falls[x] = 0.0
        else:
            peak_falls[x] += PEAK_HOLD_GRAVITY
            peaks[x] = max(levels[x], peaks[x] - peak_falls[x])

        total += levels[x]

    low = 0.0
    for x in range(6):
        if levels[x] > low:
            low = levels[x]

    bass = follow(bass, low, ENV_ATTACK, ENV_RELEASE)
    energy = follow(energy, total / WIDTH, ENV_ATTACK, ENV_RELEASE)


def draw_columns(pen):
    """One bar per column in the given pen, the top pixel faded by the fractional height so bars glide."""
    screen.pen = pen
    for x in range(WIDTH):
        level = levels[x]
        h = int(level)
        if h:
            screen.vspan(x, HEIGHT - h, h)
        frac = level - h
        if h < HEIGHT and frac > 0.04:
            screen.alpha = int(frac * 255)
            screen.hspan(x, HEIGHT - 1 - h, 1)
            screen.alpha = 255


def shifted(rows, n):
    """The 8 row pattern rolled up by n rows, so a set of these animates a scroll."""
    return tuple(rows[(i + n) % 8] for i in range(8))


class Bars:
    """The classic analyser: a green to red ramp, segmented rows, peak hold dots."""

    name = "BARS"

    def __init__(self):
        self.ramp = brush.gradient(brush.LINEAR, 0, HEIGHT, 0, 0, [
            (0.0, color.rgb(0, 200, 40)),
            (0.55, color.rgb(240, 180, 0)),
            (1.0, color.rgb(255, 20, 0)),
        ])
        self.segments = brush.pattern(color.rgb(0, 0, 0, 120), CLEAR, (0xff, 0, 0xff, 0, 0xff, 0, 0xff, 0))

    def draw(self, _bass, _energy):
        draw_columns(self.ramp)

        screen.pen = self.segments
        screen.rectangle(0, 0, WIDTH, HEIGHT)

        screen.pen = WHITE
        for x in range(WIDTH):
            p = int(peaks[x])
            if p:
                screen.hspan(x, HEIGHT - p, 1)


class Fire:
    """Bars filled with a flame texture that scrolls upward, flashing on the beat."""

    name = "FIRE"

    def __init__(self):
        flame = (0b10101010, 0b01010101, 0b11011101, 0b01110111, 0b10111011, 0b11101110, 0b01010101, 0b10101010)
        hot = color.rgb(255, 230, 60)
        ember = color.rgb(220, 40, 0)
        self.frames = [brush.pattern(hot, ember, shifted(flame, n)) for n in range(8)]
        self.flashes = [brush.lighten(90), brush.lighten(60), brush.lighten(35), brush.lighten(15)]
        self.tick = 0
        self.flash = len(self.flashes)
        self.armed = True

    def draw(self, bass, _energy):
        self.tick += 1
        draw_columns(self.frames[(self.tick >> 1) & 7])

        if bass > HEIGHT * 0.8:
            if self.armed:
                self.armed = False
                self.flash = 0
        elif bass < HEIGHT * 0.5:
            self.armed = True

        if self.flash < len(self.flashes):
            screen.pen = self.flashes[self.flash]
            screen.rectangle(0, 0, WIDTH, HEIGHT)
            self.flash += 1


class Scope:
    """An oscilloscope trace rebuilt from the four loudest bands, in a two tone hatch."""

    name = "SCOPE"

    BANDS = 4

    def __init__(self):
        self.trace = brush.pattern(color.rgb(0, 255, 120), color.rgb(0, 120, 255),
                                   (0b11001100, 0b01100110, 0b00110011, 0b10011001,
                                    0b11001100, 0b01100110, 0b00110011, 0b10011001))
        self.phase = array.array("f", bytearray(self.BANDS * 4))
        self.amp = array.array("f", bytearray(self.BANDS * 4))
        self.ys = array.array("f", bytearray(WIDTH * 4))
        self.band = WIDTH // self.BANDS

    def draw(self, _bass, _energy):
        for k in range(self.BANDS):
            a = 0.0
            for x in range(k * self.band, (k + 1) * self.band):
                if levels[x] > a:
                    a = levels[x]
            self.amp[k] = follow(self.amp[k], a, 0.4, 0.15)
            self.phase[k] += 0.05 * (k + 1)

        mid = (HEIGHT - 1) / 2

        for x in range(WIDTH):
            y = 0.0
            for k in range(self.BANDS):
                y += self.amp[k] * math.sin(2 * math.pi * (k + 1) * x / WIDTH + self.phase[k])
            y = y * mid / HEIGHT
            if y > mid:
                y = mid
            elif y < -mid:
                y = -mid
            self.ys[x] = mid + y

        screen.pen = self.trace
        for x in range(WIDTH - 1):
            screen.line(x, self.ys[x], x + 1, self.ys[x + 1])


class Waterfall:
    """A spectrogram scrolling left: bass along the bottom, treble at the top."""

    name = "FALL"

    def __init__(self):
        self.a = image(WIDTH, HEIGHT)
        self.b = image(WIDTH, HEIGHT)
        for img in (self.a, self.b):
            img.pen = BLACK
            img.clear()
        self.a_tail = self.a.window(1, 0, WIDTH - 1, HEIGHT)
        self.b_tail = self.b.window(1, 0, WIDTH - 1, HEIGHT)
        self.palette = [solid(color.oklch(10 + i * 6, 30 + i * 2, 260 - i * 14)) for i in range(16)]
        self.band = WIDTH // HEIGHT

    def draw(self, _bass, _energy):
        src, src_tail = self.a, self.a_tail
        dst = self.b
        dst.blit(src_tail, 0, 0)

        for y in range(HEIGHT):
            v = 0.0
            for x in range(y * self.band, (y + 1) * self.band):
                if levels[x] > v:
                    v = levels[x]
            dst.pen = self.palette[min(15, int(v * 16 / HEIGHT))]
            dst.hspan(WIDTH - 1, HEIGHT - 1 - y, 1)

        screen.blit(dst, 0, 0)
        self.a, self.b = dst, src
        self.a_tail, self.b_tail = self.b_tail, src_tail


class VU:
    """One big level meter with a green to red ramp, segment gaps and a peak needle."""

    name = "VU"

    def __init__(self):
        self.ramp = brush.gradient(brush.LINEAR, 0, 0, WIDTH, 0, [
            (0.0, color.rgb(0, 200, 40)),
            (0.7, color.rgb(240, 180, 0)),
            (1.0, color.rgb(255, 20, 0)),
        ])
        self.segments = brush.pattern(color.rgb(0, 0, 0, 170), CLEAR, (0b10001000,) * 8)
        self.level = 0.0
        self.peak = 0.0
        self.range = 1.0

    def draw(self, _bass, energy):
        self.range = max(energy, self.range * 0.998, 0.5)
        target = energy / self.range * (WIDTH - 1)
        self.level = follow(self.level, target, 0.5, 0.12)
        self.peak = max(self.level, self.peak - 0.15)

        w = min(WIDTH, int(self.level))
        if w:
            screen.pen = self.ramp
            screen.rectangle(0, 1, w, HEIGHT - 2)
            screen.pen = self.segments
            screen.rectangle(0, 0, WIDTH, HEIGHT)

        screen.pen = WHITE
        screen.vspan(min(WIDTH - 1, int(self.peak)), 1, HEIGHT - 2)


class Plasma:
    """Fractal noise drifting across the panel, faster on the bass, brighter with the energy."""

    name = "PLASMA"

    def __init__(self):
        self.field = brush.fractal(4.0, 2, 0.5)
        self.field.ramp([
            (0.0, color.rgb(10, 0, 50)),
            (0.4, color.rgb(180, 0, 140)),
            (0.7, color.rgb(255, 110, 0)),
            (1.0, color.rgb(255, 250, 190)),
        ])
        self.placement = mat3()
        self.drift = vec2(0.0, 0.0)
        self.shades = [brush.darken(int(160 - i * 160 / 7)) for i in range(7)]
        self.flashes = [brush.lighten(90), brush.lighten(60), brush.lighten(35), brush.lighten(15)]
        self.flash = len(self.flashes)
        self.armed = True

    def draw(self, bass, energy):
        speed = 0.3 + bass / HEIGHT * 2.0
        self.drift.x = 0.25 * speed
        self.drift.y = 0.1 * speed
        self.placement.translate(self.drift)
        self.field.transform = self.placement
        screen.pen = self.field
        screen.rectangle(0, 0, WIDTH, HEIGHT)

        shade = int(energy * 8 / HEIGHT)
        if shade < len(self.shades):
            screen.pen = self.shades[shade]
            screen.rectangle(0, 0, WIDTH, HEIGHT)

        if bass > HEIGHT * 0.75:
            if self.armed:
                self.armed = False
                self.flash = 0
        elif bass < HEIGHT * 0.45:
            self.armed = True

        if self.flash < len(self.flashes):
            screen.pen = self.flashes[self.flash]
            screen.rectangle(0, 0, WIDTH, HEIGHT)
            self.flash += 1


MODES = [Bars(), Fire(), Scope(), Waterfall(), VU(), Plasma()]


def make_label(name):
    img = image(WIDTH, HEIGHT)
    img.pen = BLACK
    img.clear()
    img.font = font.sins
    img.pen = WHITE
    w, h = img.measure_text(name, 1)
    img.text(name, (WIDTH - w) // 2, (HEIGHT - h) // 2, 1)
    return img


LABELS = [make_label(mode.name) for mode in MODES]

VOLUME_RAMP = brush.gradient(brush.LINEAR, 0, 0, WIDTH, 0, [
    (0.0, color.rgb(0, 120, 255)),
    (1.0, color.rgb(255, 255, 255)),
])

mode_index = 0
label_until = time.ticks_add(time.ticks_ms(), LABEL_MS)
volume_until = 0


def poll_knob():
    global mode_index, label_until, volume_until
    delta = enc.delta()
    if delta == 0:
        return
    if switch.value() == 0:
        volume = min(127, max(0, bt_audio.volume() + delta * VOLUME_STEP))
        bt_audio.set_volume(volume)
        volume_until = time.ticks_add(time.ticks_ms(), LABEL_MS)
        return
    mode_index = (mode_index + delta) % len(MODES)
    label_until = time.ticks_add(time.ticks_ms(), LABEL_MS)


def showing_label():
    return time.ticks_diff(label_until, time.ticks_ms()) > 0


def showing_volume():
    return time.ticks_diff(volume_until, time.ticks_ms()) > 0


def draw_volume():
    screen.pen = BLACK
    screen.clear()
    w = bt_audio.volume() * WIDTH // 127
    if w:
        screen.pen = VOLUME_RAMP
        screen.rectangle(0, 2, w, HEIGHT - 4)
    display.update()


def save_link_keys():
    if bt_audio.link_keys_dirty():
        with open(KEYS_FILE, "wb") as f:
            f.write(bt_audio.link_keys())
        print("Saved link keys")


try:
    with open(KEYS_FILE, "rb") as f:
        if not bt_audio.load_link_keys(f.read()):
            print("Stored link keys rejected, re-pair required")
except OSError:
    pass

gc.collect()
bt_audio.init(name="Widget")
print(f"Pair with 'Widget'. Turn the knob to change mode, hold the switch and turn for volume. PCM ring {bt_audio.ring_frames()} frames")

last_seq = 0
last_report = time.ticks_ms()
draw_us = 0

while True:
    save_link_keys()
    poll_knob()

    if showing_volume():
        draw_volume()
        time.sleep_ms(20)
        continue

    if showing_label():
        screen.blit(LABELS[mode_index], 0, 0)
        display.update()
        time.sleep_ms(20)
        continue

    if not bt_audio.streaming():
        screen.pen = BLACK
        screen.clear()
        display.update()
        time.sleep_ms(100)
        continue

    seq = bt_audio.fft_seq()
    if seq == last_seq:
        time.sleep_ms(2)
        continue
    last_seq = seq

    if not bt_audio.fft_into(bins):
        continue

    t = time.ticks_us()
    analyse()
    screen.pen = BLACK
    screen.clear()
    MODES[mode_index].draw(bass, energy)
    display.update()
    draw_us = max(draw_us, time.ticks_diff(time.ticks_us(), t))

    if time.ticks_diff(time.ticks_ms(), last_report) > 5000:
        last_report = time.ticks_ms()
        print(f"{MODES[mode_index].name} draw {draw_us}us "
              f"sbc {bt_audio.sbc_frames()} ring {bt_audio.ring_level()}/{bt_audio.ring_frames()} "
              f"underruns {bt_audio.underruns()}")
        draw_us = 0
