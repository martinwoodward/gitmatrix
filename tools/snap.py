"""Capture the widget's 53x7 framebuffer to a PNG so frames can be reviewed off-device.

Usage:
    python3 tools/snap.py out.png "<python to run on the device that draws to screen>"

The device code runs after `import contribution_widget`; anything it draws to `screen`
is read back pixel by pixel and rendered as scaled-up round LEDs.
"""
import subprocess
import sys

from PIL import Image, ImageDraw

W, H, SCALE = 53, 7, 14

DUMP = """
import sys
for _y in range(7):
    sys.stdout.write('ROW' + ' '.join('%x' % (screen.get(_x, _y).p & 0xffffffff) for _x in range(53)) + '\\n')
"""


def snap(code):
    script = "import contribution_widget\n" + code + "\n" + DUMP
    out = subprocess.run(["mpremote", "exec", script], capture_output=True, text=True, timeout=120)
    rows = [line[3:].split() for line in out.stdout.splitlines() if line.startswith("ROW")]
    if len(rows) != H:
        sys.exit(out.stdout + out.stderr)
    return rows


def render(rows, path):
    img = Image.new("RGB", (W * SCALE, H * SCALE), (12, 12, 12))
    d = ImageDraw.Draw(img)
    r = SCALE // 2 - 1
    for y, row in enumerate(rows):
        for x, v in enumerate(row):
            p = int(v, 16)  # 0xAABBGGRR
            rgb = (p & 0xFF, (p >> 8) & 0xFF, (p >> 16) & 0xFF)
            cx, cy = x * SCALE + SCALE // 2, y * SCALE + SCALE // 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgb if any(rgb) else (28, 28, 28))
    img.save(path)


if __name__ == "__main__":
    render(snap(sys.argv[2]), sys.argv[1])
    print("wrote", sys.argv[1])
