# Spikes 4+5: LittleFS write/rename timing and rename-over-existing semantics.
# Run: mpremote run fs_spike.py   (optionally with bt_audio streaming, see STREAM)
import os, time
STREAM = False
try:
    import bt_audio
except ImportError:
    bt_audio = None

def us(): return time.ticks_us()
def rm(p):
    try: os.remove(p)
    except OSError: pass

def underruns():
    return bt_audio.underruns() if (STREAM and bt_audio) else 0

def rename_test():
    rm('/_cfg.json'); rm('/_cfg.tmp')
    with open('/_cfg.json', 'w') as f: f.write('old')
    with open('/_cfg.tmp', 'w') as f: f.write('new')
    t = us(); os.rename('/_cfg.tmp', '/_cfg.json'); dt = time.ticks_diff(us(), t)
    with open('/_cfg.json') as f: v = f.read()
    exists_tmp = '_cfg.tmp' in os.listdir('/')
    print('RENAME over existing: ok=%s content=%r tmp_left=%s %d us' % (v == 'new', v, exists_tmp, dt))
    rm('/_cfg.json')

def write_test(size, reps=5):
    buf = bytes(size)
    ws, rs, mx = [], [], 0
    u0 = underruns()
    for i in range(reps):
        t = us()
        with open('/_w.tmp', 'wb') as f: f.write(buf)
        dw = time.ticks_diff(us(), t)
        t = us(); os.rename('/_w.tmp', '/_w.bin'); dr = time.ticks_diff(us(), t)
        ws.append(dw); rs.append(dr)
    rm('/_w.bin')
    print('WRITE %6d B: write avg %6d max %6d us | rename avg %5d max %5d us | underruns +%d' % (
        size, sum(ws)//reps, max(ws), sum(rs)//reps, max(rs), underruns() - u0))

rename_test()
for s in (1024, 8192, 65536):
    write_test(s)
