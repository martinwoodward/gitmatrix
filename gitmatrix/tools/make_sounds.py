#!/usr/bin/env python3
"""Generate GitMatrix's built-in notification sounds into firmware/sounds/.

All sounds are synthesised here from sine/square tones with simple envelopes,
so there are no third-party samples or copyright questions. Output is 16-bit
mono 22.05 kHz WAV, kept short so the set stays small on flash.

    python3 tools/make_sounds.py            # (re)write firmware/sounds/*.wav
    python3 tools/make_sounds.py --check    # fail if the committed files differ
"""

import io
import math
import struct
import sys
import wave
from pathlib import Path

RATE = 22050
PEAK = 0.6
OUT = Path(__file__).resolve().parent.parent / "firmware" / "sounds"


def tone(freq, ms, shape="sine", attack=5, decay=None, vol=1.0, harmonics=()):
    """One note: freq in Hz (or (start, end) for a glide)."""
    n = int(RATE * ms / 1000)
    f0, f1 = freq if isinstance(freq, tuple) else (freq, freq)
    a = max(1, int(RATE * attack / 1000))
    out = []
    phase = 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * i / max(1, n - 1)
        phase += 2 * math.pi * f / RATE
        if shape == "square":
            v = 1.0 if math.sin(phase) >= 0 else -1.0
            v *= 0.5
        else:
            v = math.sin(phase)
            for k, amp in harmonics:
                v += amp * math.sin(phase * k)
        env = min(1.0, i / a)
        if decay is None:
            env *= max(0.0, 1.0 - max(0, i - (n - a)) / a)       # short release
        else:
            env *= math.exp(-i / (RATE * decay / 1000))
        out.append(v * env * vol)
    return out


def rest(ms):
    return [0.0] * int(RATE * ms / 1000)


def mix(*parts):
    n = max(len(p) for p in parts)
    return [sum(p[i] for p in parts if i < len(p)) for i in range(n)]


BELL = ((2.0, 0.4), (3.0, 0.2), (4.2, 0.1))

SOUNDS = {
    # rising two-note bell
    "chime": lambda: tone(880, 260, decay=120, harmonics=BELL) + tone(1318.5, 420, decay=180, harmonics=BELL),
    # one short high blip
    "ping": lambda: tone(1760, 160, decay=50, harmonics=((2.0, 0.2),)),
    # three urgent beeps
    "alert": lambda: (tone(988, 110, "square", vol=0.7) + rest(60)) * 2 + tone(988, 110, "square", vol=0.7),
    # rising major arpeggio
    "success": lambda: tone(523.3, 90, decay=80) + tone(659.3, 90, decay=80) + tone(784, 90, decay=80) + tone(1046.5, 260, decay=140),
    # two falling low tones
    "error": lambda: tone(392, 170, "square", vol=0.6) + rest(40) + tone((330, 262), 260, "square", vol=0.6),
    # classic ding-dong
    "doorbell": lambda: tone(659.3, 420, decay=260, harmonics=BELL) + tone(523.3, 620, decay=360, harmonics=BELL),
    # game-style coin pickup
    "coin": lambda: tone(987.8, 70, "square", vol=0.5) + tone(1318.5, 260, "square", decay=110, vol=0.5),
}


def render(samples):
    peak = max(1e-9, max(abs(v) for v in samples))
    scale = PEAK * 32767 / peak
    pcm = b"".join(struct.pack("<h", max(-32768, min(32767, int(v * scale)))) for v in samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def main(argv):
    check = "--check" in argv
    OUT.mkdir(parents=True, exist_ok=True)
    bad = []
    total = 0
    for name, make in SOUNDS.items():
        data = render(make())
        total += len(data)
        path = OUT / f"{name}.wav"
        if check:
            if not path.exists() or path.read_bytes() != data:
                bad.append(name)
        else:
            path.write_bytes(data)
            print(f"{path.relative_to(OUT.parent.parent)}  {len(data)} bytes")
    if check and bad:
        print("out of date: " + ", ".join(bad))
        return 1
    print(f"{len(SOUNDS)} sounds, {total} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
