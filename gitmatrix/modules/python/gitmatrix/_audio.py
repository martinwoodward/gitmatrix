# The audio API: Art Matrix's `mic` interface, fed from the Bluetooth speaker.
#
# bt_audio's core1 publishes 512 FFT magnitude bins every ~23 ms while streaming.
# We fold them into 16 bands and a handful of features at most once per new FFT
# frame (the first call that needs them does the work), so any number of calls
# per frame agree, as with Art Matrix's display.hear().

from array import array

from gitmatrix import _compat, _flash, _runtime

try:
    import bt_audio
except ImportError:
    bt_audio = None

BANDS = 16
WAVE = 64
FIRST_BIN = 2
LAST_BIN = 240
MIN_SCALE = 300
GAIN_ATTACK = 0.2
GAIN_DECAY = 0.997
ATTACK = 0.6
RELEASE = 0.15
KICK_REFRACTORY_MS = 180
CLAP_REFRACTORY_MS = 120
SILENCE = 0.02
NAME = "GitMatrix"
KEYS_FILE = "/bt_link_keys.bin"


def _band_edges():
    edges = array("H", bytes((BANDS + 1) * 2))
    for i in range(BANDS + 1):
        t = i / BANDS
        edges[i] = FIRST_BIN + int((LAST_BIN - FIRST_BIN) * t * t)
    for i in range(BANDS):
        if edges[i + 1] <= edges[i]:
            edges[i + 1] = edges[i] + 1
    return edges


class Audio:
    """Also available as `mic` (Art Matrix name) and `gitmatrix.audio`."""

    def __init__(self, bt=None):
        self._bt = bt if bt is not None else bt_audio
        self._bins = array("H", bytes(512 * 2))
        self._raw = array("f", bytes(BANDS * 4))
        self._bands = array("f", bytes(BANDS * 4))
        self._wave = array("f", bytes(WAVE * 4))
        self._edges = _band_edges()
        self._seq = -1
        self._scale = MIN_SCALE
        self._level = 0.0
        self._bass = 0.0
        self._bass_env = 0.0
        self._high_env = 0.0
        self._tone = 0.0
        self._flux = 0.0
        self._intensity = 0.0
        self._streaming = False
        # count, strength for beat, kick, clap (same slots as Art Matrix)
        self._counts = [0, 0, 0]
        self._strengths = [0.0, 0.0, 0.0]
        self._seen = [0, 0, 0]
        self._last_kick = 0
        self._last_clap = 0
        self._intervals = []
        self._bpm = 0.0
        self._checked = -1

    # ---- driver plumbing ----------------------------------------------------

    def _call(self, name, *args, default=None):
        fn = getattr(self._bt, name, None) if self._bt is not None else None
        if fn is None:
            return default
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001 - a driver hiccup must not take down a plugin
            return default

    def _update(self):
        now = _compat.ticks_ms()
        if now == self._checked:
            return
        self._checked = now
        self._streaming = bool(self._call("streaming", default=False))
        if not self._streaming:
            self._decay()
            return
        seq = self._call("fft_seq", default=self._seq)
        if seq == self._seq:
            return
        self._seq = seq
        if self._call("fft_into", self._bins, default=False):
            self._analyse(now)

    def _decay(self):
        for i in range(BANDS):
            self._bands[i] *= 0.8
        self._level *= 0.8
        self._bass *= 0.8
        self._flux = 0.0
        self._intensity *= 0.98

    def _analyse(self, now):
        bins = self._bins
        edges = self._edges
        raw = self._raw
        peak_all = 0
        for i in range(BANDS):
            peak = 0
            for b in range(edges[i], edges[i + 1]):
                v = bins[b]
                if v > peak:
                    peak = v
            raw[i] = peak
            if peak > peak_all:
                peak_all = peak

        if peak_all > self._scale:
            self._scale += (peak_all - self._scale) * GAIN_ATTACK
        else:
            self._scale = max(MIN_SCALE, self._scale * GAIN_DECAY)

        bands = self._bands
        total = 0.0
        weighted = 0.0
        flux = 0.0
        for i in range(BANDS):
            target = raw[i] / self._scale
            if target > 1.0:
                target = 1.0
            old = bands[i]
            if target > old:
                flux += target - old
                bands[i] = old + (target - old) * ATTACK
            else:
                bands[i] = old + (target - old) * RELEASE
            total += bands[i]
            weighted += bands[i] * i

        self._level = total / BANDS
        self._tone = (weighted / total / (BANDS - 1)) if total > 0.001 else 0.0
        self._flux = flux / BANDS
        self._intensity += (self._level - self._intensity) * 0.02
        bass = (bands[0] + bands[1] + bands[2]) / 3
        high = (bands[11] + bands[12] + bands[13] + bands[14] + bands[15]) / 5
        self._bass = bass

        # Onsets: a jump well above the slow envelope, with a refractory period
        if bass > self._bass_env * 1.5 + 0.08 and _compat.ticks_diff(now, self._last_kick) > KICK_REFRACTORY_MS:
            strength = min(1.0, max(0.01, bass - self._bass_env))
            self._hit_now(1, strength)
            self._hit_now(0, strength)
            if self._last_kick:
                interval = _compat.ticks_diff(now, self._last_kick)
                if 300 <= interval <= 1500:
                    self._intervals.append(interval)
                    if len(self._intervals) > 8:
                        self._intervals.pop(0)
                    ordered = sorted(self._intervals)
                    self._bpm = 60000 / ordered[len(ordered) // 2]
            self._last_kick = now
        if high > self._high_env * 1.6 + 0.08 and _compat.ticks_diff(now, self._last_clap) > CLAP_REFRACTORY_MS:
            self._hit_now(2, min(1.0, max(0.01, high - self._high_env)))
            self._last_clap = now
        self._bass_env += (bass - self._bass_env) * 0.1
        self._high_env += (high - self._high_env) * 0.1

    def _hit_now(self, which, strength):
        self._counts[which] += 1
        self._strengths[which] = strength

    def _hit(self, which):
        self._update()
        count = self._counts[which]
        if count == self._seen[which]:
            return 0
        self._seen[which] = count
        return self._strengths[which]

    # ---- Art Matrix mic API -------------------------------------------------

    def level(self):
        """Overall loudness 0-1."""
        self._update()
        return self._level

    def tone(self):
        """Spectral centroid 0 (bassy) - 1 (bright)."""
        self._update()
        return self._tone

    def flux(self):
        """How much the spectrum rose since the last FFT frame, 0-1."""
        self._update()
        return self._flux

    def intensity(self):
        """Slow-moving loudness, for long fades."""
        self._update()
        return self._intensity

    def active(self):
        self._update()
        return self._streaming and self._level > SILENCE

    def bpm(self):
        self._update()
        return self._bpm

    def phase(self):
        """0-1 through the current beat, 0 when no tempo is known."""
        self._update()
        if not self._bpm or not self._last_kick:
            return 0.0
        period = 60000 / self._bpm
        return (_compat.ticks_diff(_compat.ticks_ms(), self._last_kick) % period) / period

    def beat(self):
        return self._hit(0)

    def kick(self):
        return self._hit(1)

    def clap(self):
        return self._hit(2)

    def spectrum(self):
        """16 bands low to high, 0-1. The same array every call; copy it to keep it."""
        self._update()
        return self._bands

    def waveform(self):
        """Zeros: bt_audio exposes no PCM to Python. Kept so Art Matrix plugins run."""
        return self._wave

    # ---- GitMatrix additions -------------------------------------------------

    def bass(self):
        self._update()
        return self._bass

    def fft(self):
        """The raw 512 FFT bins (uint16), refreshed each new frame."""
        self._update()
        return self._bins

    def streaming(self):
        self._update()
        return self._streaming

    def connected(self):
        value = self._call("connected", default=None)
        return self.streaming() if value is None else bool(value)

    def volume(self):
        """Speaker volume 0-1."""
        return (self._call("volume", default=0) or 0) / 127

    def set_volume(self, value):
        value = int(max(0.0, min(1.0, value)) * 127 + 0.5)
        self._call("set_volume", value)

    @property
    def name(self):
        return NAME


def start(bt=None, name=NAME, keys_file=None):
    """Bring the speaker up. Called once at boot; plugins never own Bluetooth."""
    bt = bt if bt is not None else bt_audio
    keys_file = keys_file or _runtime.path(KEYS_FILE)
    _flash.set_bt(bt)
    if bt is None:
        return False
    try:
        with open(keys_file, "rb") as f:
            if not bt.load_link_keys(f.read()):
                print("bt: stored link keys rejected, re-pair required")
    except OSError:
        pass
    try:
        bt.init(name=name)
    except Exception as e:  # noqa: BLE001 - no speaker is bad, but no display is worse
        print(f"bt: init failed {_compat.error_text(e)}")
        return False
    return True


def save_link_keys(bt=None, keys_file=None):
    bt = bt if bt is not None else bt_audio
    keys_file = keys_file or _runtime.path(KEYS_FILE)
    if not _flash.can_write():
        return False    # a new pairing waits until playback stops
    try:
        if bt is not None and bt.link_keys_dirty():
            _compat.write_atomic(keys_file, bt.link_keys())
            return True
    except (OSError, AttributeError) as e:
        print(f"bt: could not save link keys {_compat.error_text(e)}")
    return False


def pairable_supported(bt=None):
    bt = bt if bt is not None else bt_audio
    return bt is not None and hasattr(bt, "set_pairable")


def set_pairable(on, bt=None):
    """Whether new phones can find and pair with the speaker. Needs bt_audio
    support (set_pairable); returns False when the firmware doesn't have it."""
    bt = bt if bt is not None else bt_audio
    if not pairable_supported(bt):
        return False
    try:
        bt.set_pairable(bool(on))
    except Exception as e:  # noqa: BLE001 - never let this stop the speaker
        print(f"bt: set_pairable failed {_compat.error_text(e)}")
        return False
    return True


def forget_devices(bt=None, keys_file=None):
    """Forget every paired phone. Disconnects a playing one (the sink is paused
    for the flash write and comes back with no keys)."""
    bt = bt if bt is not None else bt_audio
    keys_file = keys_file or _runtime.path(KEYS_FILE)
    with _flash.pause():
        _flash.drop_keys()
        try:
            if _compat.exists(keys_file):
                _compat.remove_tree(keys_file)
        except OSError:
            pass
        try:
            if bt is not None and not _flash.sink_down():
                bt.load_link_keys(b"")
        except Exception:  # noqa: BLE001, S110 - best effort; keys are gone from flash either way
            return False
    return True
