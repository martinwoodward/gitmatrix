# Spike 4b: does a LittleFS write hang the device while bt_audio is connected/streaming?
# Escalates from harmless to risky and prints BEGIN/END per step, so the host can tell
# exactly which step hung. Driven by flash_host.py (handles audio + hang detection).
#   MODE = 'escalate' : connected-idle writes, then streaming writes by size/type
#   MODE = 'deinit'   : streaming -> bt_audio.deinit() -> 64 KB write + rename
import bt_audio, os, time, gc

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
