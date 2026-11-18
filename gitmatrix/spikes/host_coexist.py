#!/usr/bin/env python3
"""Host driver for coexist.py.

Streams a tone from this Mac to the 'Widget' Bluetooth output via afplay, hits the
device's HTTP server when it announces SERVER_UP, and always restores the original
default output device. Requires SwitchAudioSource (brew install switchaudio-osx) and
the Mac already paired with the widget.
"""
import math
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave

PORT = sys.argv[1] if len(sys.argv) > 1 else '/dev/cu.usbmodem2101'
WAV = '/tmp/gm/stream.wav'
HERE = os.path.dirname(os.path.abspath(__file__))


def sas(*args):
    return subprocess.run(['SwitchAudioSource', *args], capture_output=True, text=True).stdout


def make_wav():
    if os.path.exists(WAV):
        return
    os.makedirs(os.path.dirname(WAV), exist_ok=True)
    sec = b''.join(struct.pack('<hh', v, v) for v in
                   (int(8000 * math.sin(2 * math.pi * 440 * i / 44100)) for i in range(44100)))
    with wave.open(WAV, 'w') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(sec * 30)


player = None


def stop_player():
    if player and player.poll() is None:
        import signal
        os.killpg(player.pid, signal.SIGTERM)


def start_stream():
    global player
    name = None
    for _ in range(100):
        names = [l for l in sas('-a', '-t', 'output').splitlines() if l.startswith('Widget')]
        if names:
            name = names[0]
            break
        time.sleep(0.2)
    if not name:
        print('HOST: Widget output not present', flush=True)
        return
    sas('-t', 'output', '-s', name)
    stop_player()
    player = subprocess.Popen(['bash', '-c', 'while true; do afplay %s; done' % WAV],
                              start_new_session=True)
    print('HOST: streaming to', name, flush=True)


def hammer(ip, secs=8):
    ok = err = 0
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen('http://%s/' % ip, timeout=3).read()
            ok += 1
        except Exception:
            err += 1
    print('HOST: server hits ok=%d err=%d' % (ok, err), flush=True)


def main():
    make_wav()
    orig = sas('-c', '-t', 'output').strip()
    if orig.startswith('Widget'):
        orig = 'MacBook Pro Speakers'
    ip = None
    try:
        p = subprocess.Popen(['mpremote', 'connect', PORT, 'resume', 'run', os.path.join(HERE, 'coexist.py')],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            print(line, end='', flush=True)
            if line.startswith(('CONNECTED', 'RECONNECTED')):
                threading.Thread(target=start_stream, daemon=True).start()
            elif line.startswith('SERVER_IP'):
                ip = line.split()[1]
            elif line.startswith('SERVER_UP'):
                threading.Thread(target=hammer, args=(ip,), daemon=True).start()
        p.wait()
    finally:
        stop_player()
        sas('-t', 'output', '-s', orig)
        print('HOST: restored output to', orig)


if __name__ == '__main__':
    main()
