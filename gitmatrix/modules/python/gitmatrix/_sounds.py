# Alert sounds: built-in WAVs in /sounds, custom uploads in /data/sounds, and
# one playback path through machine.I2S.
#
# Phase 0 on the real board:
#   * I2S plays fine while bt_audio is initialised but idle;
#   * only the LEFT channel is audible, so mono is written to both channels;
#   * while A2DP streams, an I2S write replaces the stream for its length (no
#     hang, no underruns) and A2DP comes back by itself ~2.5 s later. That is
#     the opt-in "interrupt audio" path. Stopping the sink (deinit -> play ->
#     init) also works but the phone must reconnect, so it's only a fallback.
# Every decision about whether and how to play lives in alert() / play(), so
# the policy can change after more measurements without touching callers.

from gitmatrix import _compat, _flash, _log, _runtime

import micropython

BUILTIN = ("chime", "ping", "alert", "success", "error", "doorbell", "coin")
BUILTIN_DIR = "/sounds"
CUSTOM_DIR = "/data/sounds"

MAX_INLINE = 32 * 1024          # sound_data in a notify request
MAX_URL_BYTES = 256 * 1024      # sound_url download
MAX_CUSTOM = 256 * 1024         # one uploaded sound
MAX_CUSTOM_TOTAL = 2 * 1024 * 1024
MAX_DEFERRED = 256 * 1024       # uploads held in RAM while A2DP streams

# I2S wiring on the contribution widget
I2S_ID = 0
SCK, WS, SD, AMP_EN = 9, 10, 8, 11
CHUNK = 1024                    # frames per write
INTERRUPT_MODE = "i2s"          # "i2s" (play over the stream) or "restart" (deinit/init fallback)

_buf_in = None
_buf_out = None
playing = None                  # name/description of the sound playing, for status
plays = 0
last_result = None


def parse_wav(data):
    """(rate, channels, data offset, data length) for a 16-bit PCM WAV."""
    data = memoryview(data)
    if len(data) < 12 or bytes(data[0:4]) != b"RIFF" or bytes(data[8:12]) != b"WAVE":
        raise ValueError("not a WAV file")
    pos = 12
    fmt = None
    while pos + 8 <= len(data):
        kind = bytes(data[pos:pos + 4])
        size = int.from_bytes(bytes(data[pos + 4:pos + 8]), "little")
        body = pos + 8
        if kind == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise ValueError("bad WAV header")
            f = bytes(data[body:body + 16])
            audio_format = int.from_bytes(f[0:2], "little")
            channels = int.from_bytes(f[2:4], "little")
            rate = int.from_bytes(f[4:8], "little")
            bits = int.from_bytes(f[14:16], "little")
            if audio_format != 1 or bits != 16 or channels not in (1, 2):
                raise ValueError("WAV must be 16-bit PCM, mono or stereo")
            if not 8000 <= rate <= 48000:
                raise ValueError("WAV sample rate must be 8000-48000 Hz")
            fmt = (rate, channels)
        elif kind == b"data":
            if fmt is None:
                raise ValueError("bad WAV header")
            return fmt[0], fmt[1], body, size
        pos = body + size + (size & 1)
    raise ValueError("WAV has no data")


def valid_name(name):
    return isinstance(name, str) and 0 < len(name) <= 32 and _compat.valid_name(name)


def builtin_path(name):
    return _runtime.path(f"{BUILTIN_DIR}/{name}.wav")


def custom_path(name):
    return _runtime.path(f"{CUSTOM_DIR}/{name}.wav")


def find(name):
    """Path of a sound by name (custom sounds can't shadow built-ins)."""
    if not valid_name(name):
        return None
    if name in BUILTIN:
        p = builtin_path(name)
        return p if _compat.exists(p) else None
    p = custom_path(name)
    return p if _compat.exists(p) else None


def _size(path):
    import os
    try:
        return os.stat(path)[6]
    except OSError:
        return 0


def custom_names():
    import os
    try:
        names = os.listdir(_runtime.path(CUSTOM_DIR))
    except OSError:
        return []
    return sorted(n[:-4] for n in names if n.endswith(".wav"))


def custom_total():
    return sum(_size(custom_path(n)) for n in custom_names())


def listing():
    out = []
    for name in BUILTIN:
        p = builtin_path(name)
        if _compat.exists(p):
            out.append({"name": name, "builtin": True, "size": _size(p)})
    for name in custom_names():
        out.append({"name": name, "builtin": False, "size": _size(custom_path(name))})
    return out


# ---- custom sounds ----------------------------------------------------------

class SoundError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def check_upload(name, data):
    """Validate an upload; raises SoundError (413 when too big)."""
    if not valid_name(name):
        raise SoundError("bad sound name (letters, digits, - and _, up to 32)")
    if name in BUILTIN:
        raise SoundError(f"{name} is a built-in sound")
    if len(data) > MAX_CUSTOM:
        raise SoundError(f"sound larger than {MAX_CUSTOM // 1024} KB", 413)
    try:
        parse_wav(data)
    except ValueError as e:
        raise SoundError(str(e)) from e
    old = _size(custom_path(name))
    if custom_total() - old + len(data) > MAX_CUSTOM_TOTAL:
        raise SoundError(f"custom sounds would use more than {MAX_CUSTOM_TOTAL // 1024 // 1024} MB", 413)


_deferred = []      # (name, data) uploads waiting for the stream to stop


def save(name, data):
    """Store a custom sound. Writes go through the _flash funnel: while A2DP is
    streaming the upload is held in RAM and written when playback stops.
    Returns "saved" or "queued"."""
    check_upload(name, data)
    if not _flash.can_write():
        rest = [(n, d) for n, d in _deferred if n != name]
        if sum(len(d) for _, d in rest) + len(data) > MAX_DEFERRED:
            raise SoundError("too many sounds waiting for Bluetooth audio to stop; try again later", 503)
        rest.append((name, bytes(data)))
        _deferred[:] = rest
        _log.log(f"sounds: {name} queued until Bluetooth audio stops")
        return "queued"
    _write(name, data)
    return "saved"


def _write(name, data):
    _compat.makedirs(_runtime.path(CUSTOM_DIR))
    _compat.write_atomic(custom_path(name), data)
    _log.log(f"sounds: saved {name} ({len(data)} bytes)")


def flush():
    """Write queued uploads once flash is safe (called from housekeeping)."""
    while _deferred and _flash.can_write():
        name, data = _deferred.pop(0)
        try:
            _write(name, data)
        except OSError as e:
            _log.log(f"sounds: saving {name} failed {_compat.error_text(e)}")


def pending():
    return [n for n, _ in _deferred]


def delete(name):
    if not valid_name(name) or name in BUILTIN:
        raise SoundError("only custom sounds can be deleted")
    for i, (n, _) in enumerate(_deferred):
        if n == name:
            _deferred.pop(i)
            return True
    p = custom_path(name)
    if not _compat.exists(p):
        return False
    if playing == p:
        # removing a file play() is still reading took the device down on 1.29
        raise SoundError(f"{name} is playing; try again when it finishes", 409)
    import os
    _flash.check()
    os.remove(p)
    return True


# ---- playback ---------------------------------------------------------------

@micropython.viper
def _to_stereo(src, dst, n: int, vol: int):
    # mono 16-bit -> both channels, scaled by vol/128 (only the left is audible)
    s = ptr16(src)
    d = ptr16(dst)
    i = 0
    while i < n:
        v = int(s[i])
        if v > 32767:
            v -= 65536
        v = (v * vol) >> 7
        d[2 * i] = v & 0xFFFF
        d[2 * i + 1] = v & 0xFFFF
        i += 1


@micropython.viper
def _scale(src, dst, n: int, vol: int):
    # stereo 16-bit: copy, scaled by vol/128
    s = ptr16(src)
    d = ptr16(dst)
    i = 0
    while i < n:
        v = int(s[i])
        if v > 32767:
            v -= 65536
        v = (v * vol) >> 7
        d[i] = v & 0xFFFF
        i += 1


def _volume(config):
    vol = config.get("volume", 32) if config is not None else 32
    return max(0, min(128, (int(vol) * 128 + 63) // 127))


def _open(rate):
    import machine
    i2s = machine.I2S(I2S_ID, sck=machine.Pin(SCK), ws=machine.Pin(WS), sd=machine.Pin(SD),
                      mode=machine.I2S.TX, bits=16, format=machine.I2S.STEREO, rate=rate, ibuf=8192)
    amp = machine.Pin(AMP_EN, machine.Pin.OUT)
    return i2s, amp


def _writer(i2s):
    if hasattr(i2s, "drain"):
        return i2s
    import asyncio
    return asyncio.StreamWriter(i2s, {})


async def play(source, config=None):
    """Play a WAV (path or bytes) through I2S without blocking the loop.
    Cancel the task to stop it. Returns the number of frames played."""
    global _buf_in, _buf_out, playing, plays
    if _buf_in is None:
        _buf_in = bytearray(CHUNK * 4)
        _buf_out = bytearray(CHUNK * 4)
    f = None
    if isinstance(source, str):
        f = open(source, "rb")
        head = f.read(512)
    else:
        head = source
    rate, channels, offset, length = parse_wav(head)
    if f is None:
        length = min(length, len(source) - offset)
    vol = _volume(config)
    frames = 0
    i2s = amp = None
    playing = source if isinstance(source, str) else "data"
    try:
        i2s, amp = _open(rate)
        out = _writer(i2s)
        amp.value(1)
        if f is not None:
            f.seek(offset)
        mv = None if f is not None else memoryview(source)
        pos = offset
        end = offset + length
        step = CHUNK * 2 * channels        # bytes of input per chunk
        while pos < end:
            want = min(step, end - pos)
            if f is not None:
                got = f.readinto(memoryview(_buf_in)[:want])
                if not got:
                    break
            else:
                got = want
                _buf_in[:got] = mv[pos:pos + got]
            got &= ~1
            n = got // 2
            if channels == 1:
                _to_stereo(_buf_in, _buf_out, n, vol)
                nbytes = n * 4
                frames += n
            else:
                _scale(_buf_in, _buf_out, n, vol)
                nbytes = n * 2
                frames += n // 2
            out.write(memoryview(_buf_out)[:nbytes])
            await out.drain()
            pos += got
        plays += 1
        return frames
    finally:
        playing = None
        if amp is not None:
            amp.value(0)
        if i2s is not None:
            i2s.deinit()
        if f is not None:
            f.close()


async def alert(source, high, config, force=False):
    """Play a notification's (or POST /sound's) sound if the alert settings
    allow it. The one place that decides what happens while Bluetooth audio is
    streaming."""
    global last_result
    from gitmatrix import _notify

    streaming = _flash.streaming()
    policy = _notify.sound_policy(config, high, streaming, force=force)
    last_result = policy
    if policy in ("off", "quiet"):
        return policy
    if policy == "streaming":
        _log.log("sounds: Bluetooth audio is streaming, notification shown silently")
        return policy
    try:
        if policy == "interrupt" and INTERRUPT_MODE == "restart":
            _log.log("sounds: stopping Bluetooth audio for a high-priority alert; the source must reconnect")
            with _flash.pause():
                await play(source, config)
        else:
            if policy == "interrupt":
                _log.log("sounds: high-priority alert plays over Bluetooth audio")
            await play(source, config)
    except (OSError, ValueError) as e:
        last_result = f"error: {_compat.error_text(e)}"
        _log.log(f"sounds: {last_result}")
    return policy
