#!/usr/bin/env python3
"""Host driver for flash_stream.py: streams audio on START_STREAM, flags a hang after
25 s of silence, and always restores the Mac's output device.
Usage: flash_host.py [escalate|deinit]   (user must connect the Mac to 'Widget')."""
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else 'escalate'
PORT = '/dev/cu.usbmodem2101'
WAV = '/tmp/gm/stream.wav'  # created by host_coexist.py
HERE = os.path.dirname(os.path.abspath(__file__))


def sas(*a):
    return subprocess.run(['SwitchAudioSource', *a], capture_output=True, text=True).stdout


orig = sas('-c', '-t', 'output').strip()
if orig.startswith('Widget'):
    orig = 'MacBook Pro Speakers'
src = open(os.path.join(HERE, 'flash_stream.py')).read().replace("MODE = 'escalate'", "MODE = %r" % MODE)
tmp = tempfile.NamedTemporaryFile('w', suffix='.py', delete=False)
tmp.write(src)
tmp.close()
player = None
try:
    p = subprocess.Popen(['mpremote', 'connect', PORT, 'run', tmp.name],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    q = queue.Queue()
    threading.Thread(target=lambda: [q.put(l) for l in p.stdout] + [q.put(None)], daemon=True).start()
    waiting = True
    while True:
        try:
            line = q.get(timeout=300 if waiting else 25)
        except queue.Empty:
            print('HOST: HANG (no output for 25 s)', flush=True)
            p.kill()
            break
        if line is None:
            break
        print(line, end='', flush=True)
        if line.startswith('WAITING_FOR_SOURCE'):
            waiting = True
        elif line.startswith('CONNECTED'):
            waiting = False
        elif line.startswith('START_STREAM'):
            name = [l for l in sas('-a', '-t', 'output').splitlines() if l.startswith('Widget')]
            if name:
                sas('-t', 'output', '-s', name[0])
                player = subprocess.Popen(['bash', '-c', 'while true; do afplay %s; done' % WAV],
                                          start_new_session=True)
                print('HOST: streaming', flush=True)
            else:
                print('HOST: Widget output missing', flush=True)
finally:
    if player and player.poll() is None:
        os.killpg(player.pid, signal.SIGTERM)
    sas('-t', 'output', '-s', orig)
    os.unlink(tmp.name)
    print('HOST: restored output to', orig, flush=True)
