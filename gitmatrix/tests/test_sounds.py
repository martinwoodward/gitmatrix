# Alert sounds: WAV parsing, the I2S playback path, the streaming policy and
# custom uploads through the flash funnel.

import asyncio
import os
import struct

import pytest
import stubs

from gitmatrix import _sounds
from gitmatrix._sounds import SoundError


def wav(frames=100, rate=22050, channels=1, value=1000):
    pcm = struct.pack(f"<{frames * channels}h", *([value] * frames * channels))
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * 2 * channels, 2 * channels, 16)
    extra = b"LIST" + struct.pack("<I", 3) + b"abc\x00"     # odd chunk, padded
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + extra + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


@pytest.fixture(autouse=True)
def fresh_i2s():
    stubs.I2S.opened.clear()
    stubs.Pin.history.clear()
    _sounds.last_result = None


def test_parse_wav():
    rate, ch, off, size = _sounds.parse_wav(wav(10, 16000, 2))
    assert (rate, ch, size) == (16000, 2, 40)
    with pytest.raises(ValueError):
        _sounds.parse_wav(b"RIFF\0\0\0\0WAVEjunk")
    with pytest.raises(ValueError):
        _sounds.parse_wav(wav(rate=96000))
    eight_bit = bytearray(wav())
    eight_bit[34] = 8
    with pytest.raises(ValueError):
        _sounds.parse_wav(bytes(eight_bit))


def test_builtin_sounds_are_valid_and_small(root):
    total = 0
    for name in _sounds.BUILTIN:
        path = _sounds.find(name)
        assert path, name
        data = open(path, "rb").read()
        assert _sounds.parse_wav(data)[:2] == (22050, 1)
        total += len(data)
    assert total < 200 * 1024
    assert [s["name"] for s in _sounds.listing()] == list(_sounds.BUILTIN)


def test_mono_played_on_both_channels_with_volume(root):
    frames = asyncio.run(_sounds.play(wav(3000, value=1000), Cfg(volume=127)))
    i2s = stubs.I2S.opened[-1]
    s = i2s.samples()
    assert frames == 3000 and len(s) == 6000 and i2s.rate == 22050
    assert set(s) == {1000}
    assert i2s.closed and stubs.Pin.history[-1] == (_sounds.AMP_EN, 0)
    assert (_sounds.AMP_EN, 1) in stubs.Pin.history
    asyncio.run(_sounds.play(wav(10, value=-1000), Cfg(volume=64)))
    assert set(stubs.I2S.opened[-1].samples()) == {-508}           # 64/127 of full scale, rounded down


def test_stereo_file_played_from_flash(root):
    path = str(root / "s.wav")
    open(path, "wb").write(wav(50, 44100, 2, 200))
    assert asyncio.run(_sounds.play(path, Cfg(volume=127))) == 50
    assert stubs.I2S.opened[-1].samples() == [200] * 100


def test_cancelling_play_turns_amp_off(root):
    async def go():
        task = asyncio.create_task(_sounds.play(wav(20000), None))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert stubs.I2S.opened[-1].closed and stubs.Pin.history[-1] == (_sounds.AMP_EN, 0)
    assert _sounds.playing is None


def test_streaming_skips_sound_by_default(root, bt):
    bt.streaming_now = True
    assert asyncio.run(_sounds.alert(wav(), True, Cfg())) == "streaming"
    assert stubs.I2S.opened == [] and bt.deinits == 0


def test_interrupt_plays_over_the_stream(root, bt):
    bt.streaming_now = True
    cfg = Cfg(alert_interrupt_audio=True)
    assert asyncio.run(_sounds.alert(wav(), False, cfg)) == "streaming"   # normal priority
    assert asyncio.run(_sounds.alert(wav(), True, cfg)) == "interrupt"
    assert len(stubs.I2S.opened) == 1 and bt.deinits == 0 and bt.inits == 0


def test_interrupt_restart_fallback(root, bt, monkeypatch):
    monkeypatch.setattr(_sounds, "INTERRUPT_MODE", "restart")
    bt.streaming_now = True
    asyncio.run(_sounds.alert(wav(), True, Cfg(alert_interrupt_audio=True)))
    assert len(stubs.I2S.opened) == 1 and bt.deinits == 1 and bt.inits == 1


def test_muted_and_bad_sound(root):
    assert asyncio.run(_sounds.alert(wav(), True, Cfg(alert_sound=False))) == "off"
    assert stubs.I2S.opened == []
    asyncio.run(_sounds.alert(b"RIFFnope", False, Cfg()))
    assert _sounds.last_result.startswith("error")


# ---- custom sounds ------------------------------------------------------------

def test_upload_limits(root, monkeypatch):
    with pytest.raises(SoundError):
        _sounds.save("chime", wav())
    with pytest.raises(SoundError):
        _sounds.save("../x", wav())
    with pytest.raises(SoundError):
        _sounds.save("x", b"not a wav")
    with pytest.raises(SoundError) as e:
        _sounds.save("big", wav(_sounds.MAX_CUSTOM // 2))
    assert e.value.status == 413
    monkeypatch.setattr(_sounds, "MAX_CUSTOM_TOTAL", 2500)
    assert _sounds.save("a", wav(600)) == "saved"
    assert _sounds.save("a", wav(700)) == "saved"           # replacing doesn't double count
    with pytest.raises(SoundError) as e:
        _sounds.save("b", wav(700))
    assert e.value.status == 413


def test_upload_find_and_delete(root):
    assert _sounds.save("door", wav()) == "saved"
    assert _sounds.find("door").endswith("/data/sounds/door.wav")
    assert {"name": "door", "builtin": False, "size": len(wav())} in _sounds.listing()
    assert _sounds.delete("door") and _sounds.find("door") is None
    assert not _sounds.delete("door")
    with pytest.raises(SoundError):
        _sounds.delete("chime")


def test_upload_while_streaming_is_queued_then_written(root, bt, monkeypatch):
    monkeypatch.setattr(_sounds, "MAX_DEFERRED", 1500)
    bt.streaming_now = True
    assert _sounds.save("tune", wav(400)) == "queued"
    assert _sounds.save("tune", wav(5)) == "queued"         # replaces the queued one
    assert _sounds.save("other", wav(400)) == "queued"
    with pytest.raises(SoundError) as e:                    # RAM budget, not a 409
        _sounds.save("third", wav(400))
    assert e.value.status == 503
    assert _sounds.pending() == ["tune", "other"] and _sounds.find("tune") is None
    _sounds.flush()
    assert _sounds.find("tune") is None
    bt.streaming_now = False
    _sounds.flush()
    assert os.path.getsize(_sounds.find("tune")) == len(wav(5)) and _sounds.pending() == []
    assert _sounds.find("other")
