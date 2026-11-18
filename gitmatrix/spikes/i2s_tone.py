# Spike 2: play a tone via machine.I2S + AMP_EN (GP11). Speaker uses LEFT channel only.
import machine, math, struct, time
from machine import Pin, I2S
RATE = 22050
FREQ = 1000
SECS = 3

def make_period(freq=FREQ, rate=RATE, amp=12000):
    n = rate // freq  # 22 samples ~1002 Hz
    b = bytearray(n * 4)
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * i / n))
        struct.pack_into('<hh', b, i * 4, v, v)
    return b

def play(secs=SECS, freq=FREQ):
    amp_en = Pin(11, Pin.OUT)
    t0 = time.ticks_ms()
    i2s = I2S(0, sck=Pin(9), ws=Pin(10), sd=Pin(8), mode=I2S.TX, bits=16,
              format=I2S.STEREO, rate=RATE, ibuf=8192)
    t_open = time.ticks_diff(time.ticks_ms(), t0)
    amp_en.value(1)
    p = make_period(freq)
    chunk = bytes(p) * (1024 // len(p))
    end = time.ticks_add(time.ticks_ms(), secs * 1000)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        i2s.write(chunk)
    amp_en.value(0)
    i2s.deinit()
    print('I2S open ms', t_open, 'played', secs, 's at', freq, 'Hz')

if __name__ == '__main__':
    play()
