# Spike 3/4/2c: A2DP sink + WiFi/flash/I2S coexistence. Device side.
# Needs /wifi_tmp.json {"ssid":..,"password":..}. Driven by host_coexist.py, which
# streams audio to the device from the Mac and hits the HTTP server when asked.
import bt_audio, time, gc, json, os, network, socket, machine
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio


def keys():
    try:
        with open('/bt_link_keys.bin', 'rb') as f:
            bt_audio.load_link_keys(f.read())
    except OSError:
        pass


class Mon:
    """Samples ring_level every 10 ms (soft timer) to find the minimum per phase."""
    def __init__(self):
        self.min = 1 << 30
        self.n = 0
        self.t = machine.Timer(period=10, callback=self.cb)

    def cb(self, _):
        r = bt_audio.ring_level()
        self.n += 1
        if r < self.min:
            self.min = r

    def reset(self):
        self.min = 1 << 30
        self.n = 0

    def stop(self):
        self.t.deinit()


mon = None


def phase(name, fn):
    gc.collect()
    u0 = bt_audio.underruns()
    mon.reset()
    t = time.ticks_ms()
    try:
        info = fn()
    except Exception as e:
        info = 'ERR ' + repr(e)
    dt = time.ticks_diff(time.ticks_ms(), t)
    print('PHASE %-14s %6d ms underruns +%d ring_min %d samples %d streaming %s | %s' % (
        name, dt, bt_audio.underruns() - u0, mon.min, mon.n, bt_audio.streaming(), info))


def wait_for(pred, timeout_s):
    t = time.ticks_ms()
    while not pred():
        if time.ticks_diff(time.ticks_ms(), t) > timeout_s * 1000:
            return None
        time.sleep_ms(50)
    return time.ticks_diff(time.ticks_ms(), t)


def idle(s=8):
    time.sleep(s)
    return ''


def http_get(host, path='/', tls=False):
    import ssl
    ai = socket.getaddrinfo(host, 443 if tls else 80)[0][-1]
    s = socket.socket()
    s.settimeout(10)
    s.connect(ai)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_NONE
        s = ctx.wrap_socket(s, server_hostname=host)
    s.write(b'GET %s HTTP/1.0\r\nHost: %s\r\n\r\n' % (path.encode(), host.encode()))
    n = 0
    while True:
        d = s.read(1024)
        if not d:
            break
        n += len(d)
    s.close()
    return n


def fetches(tls, k=3):
    out = []
    for _ in range(k):
        t = time.ticks_ms()
        n = http_get('example.com', tls=tls)
        out.append('%dB/%dms' % (n, time.ticks_diff(time.ticks_ms(), t)))
    return ' '.join(out)


def server(secs=10):
    hits = [0]

    async def handle(r, w):
        await r.readline()
        while (await r.readline()) not in (b'\r\n', b''):
            pass
        w.write(b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n' + b'x' * 2048)
        await w.drain()
        w.close()
        await w.wait_closed()
        hits[0] += 1

    async def main():
        srv = await asyncio.start_server(handle, '0.0.0.0', 80)
        print('SERVER_UP')
        await asyncio.sleep(secs)
        srv.close()
        await srv.wait_closed()

    asyncio.run(main())
    return 'hits %d' % hits[0]


def flash_writes():
    out = []
    for size in (1024, 8192, 65536):
        buf = bytes(size)
        t = time.ticks_ms()
        with open('/_w.tmp', 'wb') as f:
            f.write(buf)
        os.rename('/_w.tmp', '/_w.bin')
        out.append('%dK/%dms' % (size // 1024, time.ticks_diff(time.ticks_ms(), t)))
    os.remove('/_w.bin')
    return ' '.join(out)


def i2s_open_while_streaming():
    from machine import I2S, Pin
    i2s = I2S(0, sck=Pin(9), ws=Pin(10), sd=Pin(8), mode=I2S.TX, bits=16,
              format=I2S.STEREO, rate=22050, ibuf=8192)
    time.sleep(3)
    i2s.deinit()
    time.sleep(3)
    return 'opened+idle 3s, deinit'


def main():
    global mon
    with open('/wifi_tmp.json') as f:
        cfg = json.load(f)
    if not bt_audio.connected():
        keys()
        gc.collect()
        t = time.ticks_ms()
        bt_audio.init(name='Widget')
        print('INIT %d ms' % time.ticks_diff(time.ticks_ms(), t))
    print('WAITING_FOR_SOURCE')
    print('CONNECTED after %s ms' % wait_for(bt_audio.connected, 240))
    print('STREAMING after %s ms' % wait_for(bt_audio.streaming, 30))
    print('rate', bt_audio.sample_rate(), 'ring_frames', bt_audio.ring_frames())
    mon = Mon()
    sta = network.WLAN(network.STA_IF)
    ap = network.WLAN(network.AP_IF)
    phase('idle', idle)

    def sta_connect():
        sta.active(True)
        sta.connect(cfg['ssid'], cfg['password'])
        ms = wait_for(sta.isconnected, 20)
        return 'ip %s in %s ms' % (sta.ifconfig()[0], ms)

    phase('sta_connect', sta_connect)
    phase('sta_idle', idle)
    phase('scan', lambda: '%d nets' % len(sta.scan()))
    phase('http', lambda: fetches(False))
    phase('tls', lambda: fetches(True))
    print('SERVER_IP', sta.ifconfig()[0])
    phase('server', server)

    def ap_up():
        ap.config(ssid='GitMatrix-test', key='gitmatrix123')
        ap.active(True)
        return ap.ifconfig()[0]

    phase('ap_up(sta+ap)', ap_up)
    phase('sta+ap_idle', idle)

    def sta_off():
        sta.disconnect()
        sta.active(False)
        return ''

    phase('sta_off(ap)', sta_off)
    phase('ap_idle', idle)

    def ap_off():
        ap.active(False)
        return ''

    phase('ap_off', ap_off)
    phase('wifi_off_idle', idle)
    phase('flash_writes', flash_writes)
    phase('flash_idle', lambda: idle(4))
    phase('i2s_open', i2s_open_while_streaming)
    phase('post_i2s_idle', idle)
    # 2c: pause A2DP sink -> (notification would play here) -> resume, measure reconnect.
    mon.stop()
    t = time.ticks_ms()
    bt_audio.deinit()
    print('DEINIT %d ms' % time.ticks_diff(time.ticks_ms(), t))
    time.sleep(2)
    keys()
    t = time.ticks_ms()
    bt_audio.init(name='Widget')
    print('REINIT %d ms' % time.ticks_diff(time.ticks_ms(), t))
    print('WAITING_FOR_SOURCE')
    print('RECONNECTED after %s ms' % wait_for(bt_audio.connected, 240))
    print('RESTREAM after %s ms' % wait_for(bt_audio.streaming, 30))
    time.sleep(3)
    print('final underruns', bt_audio.underruns())
    bt_audio.deinit()
    print('DONE')


main()
