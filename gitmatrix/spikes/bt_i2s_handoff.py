# Spike 2b/2d: can machine.I2S play after bt_audio.deinit()? while bt_audio is active?
import bt_audio, time, gc, sys
sys.path.append('/')
import i2s_tone

def load_keys():
    try:
        with open('/bt_link_keys.bin', 'rb') as f: bt_audio.load_link_keys(f.read())
    except OSError: pass

MODE = 'after_deinit'   # or 'while_active'
load_keys(); gc.collect()
t = time.ticks_ms(); bt_audio.init(name="Widget"); print('init ms', time.ticks_diff(time.ticks_ms(), t))
time.sleep(1)
if MODE == 'while_active':
    for bus in (0, 1):
        try:
            from machine import I2S, Pin
            i2s = I2S(bus, sck=Pin(9), ws=Pin(10), sd=Pin(8), mode=I2S.TX, bits=16,
                      format=I2S.STEREO, rate=22050, ibuf=8192)
            print('bus', bus, 'opened while bt active'); i2s.deinit()
        except Exception as e:
            print('bus', bus, 'open failed:', repr(e))
    time.sleep(1)
    print('MARK play'); i2s_tone.play(2)
    print('bt still ok? underruns', bt_audio.underruns())
    bt_audio.deinit()
else:
    t = time.ticks_ms(); bt_audio.deinit(); print('deinit ms', time.ticks_diff(time.ticks_ms(), t))
    time.sleep(1)
    print('MARK play'); i2s_tone.play(2)
    t = time.ticks_ms(); load_keys(); bt_audio.init(name="Widget")
    print('re-init ms', time.ticks_diff(time.ticks_ms(), t))
    time.sleep(1); bt_audio.deinit(); print('done')
