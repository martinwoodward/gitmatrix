# Spike 4b: does a LittleFS write hang the device while bt_audio is connected/streaming?
# Escalates from harmless to risky and prints BEGIN/END per step, so the host can tell
# exactly which step hung. Driven by flash_host.py (handles audio + hang detection).
#   MODE = 'escalate' : connected-idle writes, then streaming writes by size/type
#   MODE = 'deinit'   : streaming -> bt_audio.deinit() -> 64 KB write + rename
#   MODE = 'threshold': streaming writes of 16 KB then 32 KB (informational)
#   MODE = 'repeat64' : 3x 64 KB while streaming, then 3x with a 10 ms Timer polling ring_level
#   MODE = 'wifi64'   : while streaming: STA scan + AP up/down, then coexist-style 1/8/64 KB write+rename x2
#   MODE = 'i2s'      : open machine.I2S and play a 1 kHz tone while A2DP streams
import bt_audio, os, time, gc, machine

MODE = 'escalate'


def keys():
    try:
        with open('/bt_link_keys.bin', 'rb') as f:
            bt_audio.load_link_keys(f.read())
    except OSError:
        pass


def wait_for(pred, timeout_s):
    t = time.ticks_ms()
    while not pred():
        if time.ticks_diff(time.ticks_ms(), t) > timeout_s * 1000:
            return None
        time.sleep_ms(50)
    return time.ticks_diff(time.ticks_ms(), t)


def rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def step(name, fn):
    print('BEGIN', name, 'streaming', bt_audio.streaming(), 'underruns', bt_audio.underruns())
    time.sleep_ms(300)  # let the line reach the host before a possible hang
    t = time.ticks_us()
    fn()
    print('END  ', name, time.ticks_diff(time.ticks_us(), t), 'us')


def write(path, size):
    def f():
        with open(path, 'wb') as fh:
            if size:
                fh.write(bytes(size))
    return f


def rename(a, b):
    return lambda: os.rename(a, b)


def main():
    keys()
    gc.collect()
    bt_audio.init(name='Widget')
    print('WAITING_FOR_SOURCE')
    print('CONNECTED after', wait_for(bt_audio.connected, 240), 'ms')
    time.sleep(2)
    if MODE == 'escalate':
        step('idle_create_empty', write('/_a.tmp', 0))
        step('idle_write_4k', write('/_a.tmp', 4096))
        step('idle_write_64k', write('/_b.tmp', 65536))
        rm('/_a.tmp'); rm('/_b.tmp')
        print('START_STREAM')
        print('STREAMING after', wait_for(bt_audio.streaming, 30), 'ms')
        time.sleep(3)
        step('stream_create_empty', write('/_a.tmp', 0))
        step('stream_write_256', write('/_a.tmp', 256))
        step('stream_write_4k', write('/_a.tmp', 4096))
        step('stream_rename', rename('/_a.tmp', '/_b.tmp'))
        step('stream_write_8k', write('/_c.tmp', 8192))
        step('stream_write_64k', write('/_c.tmp', 65536))
        rm('/_b.tmp'); rm('/_c.tmp')
    elif MODE == 'threshold':
        print('START_STREAM')
        print('STREAMING after', wait_for(bt_audio.streaming, 30), 'ms')
        time.sleep(3)
        step('stream_write_16k', write('/_c.tmp', 16384))
        step('stream_write_32k', write('/_c.tmp', 32768))
        rm('/_c.tmp')
    elif MODE == 'repeat64':
        print('START_STREAM')
        print('STREAMING after', wait_for(bt_audio.streaming, 30), 'ms')
        time.sleep(3)
        for i in range(3):
            step('stream_write_64k_%d' % i, write('/_c.tmp', 65536))
        n = [0]
        tm = machine.Timer(period=10, callback=lambda _: (bt_audio.ring_level(), n.__setitem__(0, n[0] + 1)))
        for i in range(3):
            step('stream_write_64k_timer_%d' % i, write('/_c.tmp', 65536))
            step('stream_rename_timer_%d' % i, rename('/_c.tmp', '/_d.tmp'))
        tm.deinit()
        print('timer ticks', n[0])
        rm('/_c.tmp'); rm('/_d.tmp')
    elif MODE == 'wifi64':
        import network
        print('START_STREAM')
        print('STREAMING after', wait_for(bt_audio.streaming, 30), 'ms')
        time.sleep(3)
        sta = network.WLAN(network.STA_IF)
        ap = network.WLAN(network.AP_IF)
        step('sta_scan', lambda: (sta.active(True), print('nets', len(sta.scan()))))
        step('ap_up', lambda: (ap.config(ssid='GitMatrix-test', key='gitmatrix123'), ap.active(True)))
        time.sleep(5)
        step('sta_off', lambda: sta.active(False))
        step('ap_off', lambda: ap.active(False))
        time.sleep(8)
        for r in range(2):
            for size in (1024, 8192, 65536):
                step('w%d_%dk' % (r, size // 1024), write('/_w.tmp', size))
                step('rn%d_%dk' % (r, size // 1024), rename('/_w.tmp', '/_w.bin'))
        rm('/_w.bin')
    elif MODE == 'i2s':
        import i2s_tone
        print('START_STREAM')
        print('STREAMING after', wait_for(bt_audio.streaming, 30), 'ms')
        time.sleep(4)
        print('TONE_START', time.ticks_ms())
        step('i2s_tone_3s', lambda: i2s_tone.play(3, 1000))
        print('TONE_END', 'streaming', bt_audio.streaming())
        time.sleep(4)
    else:
        print('START_STREAM')
        print('STREAMING after', wait_for(bt_audio.streaming, 30), 'ms')
        time.sleep(3)
        t = time.ticks_ms()
        bt_audio.deinit()
        print('DEINIT', time.ticks_diff(time.ticks_ms(), t), 'ms')
        step('deinit_write_64k', write('/_c.tmp', 65536))
        step('deinit_rename', rename('/_c.tmp', '/_d.tmp'))
        rm('/_d.tmp')
        keys()
        t = time.ticks_ms()
        bt_audio.init(name='Widget')
        print('REINIT', time.ticks_diff(time.ticks_ms(), t), 'ms')
    print('DONE underruns', bt_audio.underruns())
    bt_audio.deinit()


main()
