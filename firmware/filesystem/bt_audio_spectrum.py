import gc
import time
import array
import contribution_widget  # noqa: F401
from encoder import Encoder
import bt_audio

"""
Bluetooth A2DP spectrum analyser, with the encoder as a volume knob.

Pair with "Widget" and play something. The BTstack run loop and the SBC decode
run on core0; core1 drives I2S and computes the FFT. This loop only reads the
published magnitude bins and draws them.

The draw loop allocates nothing. A GC pass here would stall the BTstack run
loop, and the audio path would hear it as an underrun.
"""

WIDTH = screen.width  # noqa: F821
HEIGHT = screen.height  # noqa: F821

# The lowest bins are mostly DC and rumble, and the top of a 512 bin FFT at
# 44.1kHz is above 20kHz, so only the interesting span is mapped to the display.
FIRST_BIN = 2
LAST_BIN = 240

# Bin magnitude that lights the top row. Tune to taste.
FULL_SCALE = 900

# Columns fall this many rows per frame once the peak has passed.
DECAY = 0.4

KEYS_FILE = "bt_link_keys.bin"

# The encoder driver hardcodes PIO 1, state machine 0. Claim it before the audio
# path claims a free state machine for I2S.
gc.collect()
enc = Encoder(1, 0, (22, 21))

BLACK = color.rgb(0, 0, 0)  # noqa: F821
# One colour per row, green through amber to red.
ROW_COLOURS = [
    color.rgb(0, 200, 40),  # noqa: F821
    color.rgb(60, 210, 30),  # noqa: F821
    color.rgb(130, 220, 20),  # noqa: F821
    color.rgb(200, 220, 10),  # noqa: F821
    color.rgb(240, 180, 0),  # noqa: F821
    color.rgb(250, 100, 0),  # noqa: F821
    color.rgb(255, 20, 0),  # noqa: F821
]

# Preallocated, so drawing never touches the heap.
bins = array.array("H", bytearray(512 * 2))
levels = array.array("f", bytearray(WIDTH * 4))

# A roughly logarithmic bin range per column, so bass does not occupy four
# fifths of the display.
bin_edges = array.array("H", bytearray((WIDTH + 1) * 2))
for x in range(WIDTH + 1):
    t = x / WIDTH
    bin_edges[x] = FIRST_BIN + int((LAST_BIN - FIRST_BIN) * (t * t))
for x in range(WIDTH):
    if bin_edges[x + 1] <= bin_edges[x]:
        bin_edges[x + 1] = bin_edges[x] + 1


def draw():
    screen.pen = BLACK  # noqa: F821
    screen.clear()  # noqa: F821

    for x in range(WIDTH):
        peak = 0
        for b in range(bin_edges[x], bin_edges[x + 1]):
            v = bins[b]
            if v > peak:
                peak = v

        height = peak * HEIGHT / FULL_SCALE
        if height > HEIGHT:
            height = HEIGHT

        if height > levels[x]:
            levels[x] = height
        else:
            levels[x] = max(0.0, levels[x] - DECAY)

        for y in range(int(levels[x])):
            screen.pen = ROW_COLOURS[y]  # noqa: F821
            # Row 0 of the bar is the bottom of the panel.
            screen.rectangle(x, HEIGHT - 1 - y, 1, 1)  # noqa: F821

    display.update()  # noqa: F821


def save_link_keys():
    # BTstack stores keys from the run loop, where writing flash is unsafe, so
    # it only sets a dirty flag. We do the actual write from here.
    if bt_audio.link_keys_dirty():
        with open(KEYS_FILE, "wb") as f:
            f.write(bt_audio.link_keys())
        print("Saved link keys")


def poll_volume():
    delta = enc.delta()
    if delta == 0:
        return
    volume = min(127, max(0, bt_audio.volume() + delta * 4))
    # Also tells the phone, so its slider follows the knob.
    bt_audio.set_volume(volume)


# BTstack keeps pairings in RAM. Restore them before init(), or every reset
# would need a re-pair.
try:
    with open(KEYS_FILE, "rb") as f:
        if not bt_audio.load_link_keys(f.read()):
            print("Stored link keys rejected, re-pair required")
except OSError:
    pass

bt_audio.init(name="Widget")
print(f"Pair with 'Widget'. PCM ring {bt_audio.ring_frames()} frames")

last_seq = 0
last_report = time.ticks_ms()

while True:
    save_link_keys()
    poll_volume()

    if not bt_audio.streaming():
        screen.pen = BLACK  # noqa: F821
        screen.clear()  # noqa: F821
        display.update()  # noqa: F821
        time.sleep_ms(100)
        continue

    seq = bt_audio.fft_seq()
    if seq == last_seq:
        # No new FFT frame yet, one lands every ~23ms at 44.1kHz.
        time.sleep_ms(2)
        continue
    last_seq = seq

    if not bt_audio.fft_into(bins):
        continue

    draw()

    if time.ticks_diff(time.ticks_ms(), last_report) > 5000:
        last_report = time.ticks_ms()
        # sbc_frames should settle in 60..80, underruns should stop climbing,
        # and fill should stay well under budget.
        fft_us, fill_us, budget_us = bt_audio.timings()
        print(f"{bt_audio.sample_rate()}Hz vol {bt_audio.volume()} "
              f"sbc {bt_audio.sbc_frames()} ring {bt_audio.ring_level()}/{bt_audio.ring_frames()} "
              f"underruns {bt_audio.underruns()} "
              f"fft {fft_us}us fill {fill_us}/{budget_us}us")
