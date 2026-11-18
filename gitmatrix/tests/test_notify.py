# Notifications: request validation, the queue, quiet hours, layout and the
# host taking over (and giving back) the display.

import asyncio
import base64

import pytest
import stubs
from conftest import make_plugin, set_plugins

import gitmatrix
from gitmatrix import _host, _notify, _png, _sounds
from gitmatrix._notify import NotifyError, Queue, parse


def b64(data):
    return base64.b64encode(data).decode()


def png(w, h, rgba=(255, 0, 0, 255)):
    return stubs.make_png(w, h, lambda x, y: rgba)


def wav(frames=100, rate=22050, channels=1, value=1000):
    import struct
    pcm = struct.pack(f"<{frames * channels}h", *([value] * frames * channels))
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * 2 * channels, 2 * channels, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def status(body, **kw):
    with pytest.raises(NotifyError) as e:
        parse(body, 0, **kw)
    return e.value.status


# ---- validation ---------------------------------------------------------------

def test_text_note_defaults(root):
    n = parse({"text": "hello"}, 0)
    assert n.text == "hello" and n.id and not n.high and n.repeat == 1 and n.duration is None
    assert n.color == (255, 255, 255) and n.speed == _notify.SPEED


def test_rejects_bad_bodies(root):
    assert status({}) == 400
    assert status({"txt": "x"}) == 400
    assert status([1]) == 400
    assert status({"text": "x", "duration": 61}) == 400
    assert status({"text": "x", "duration": 0}) == 400
    assert status({"text": "x", "repeat": 6}) == 400
    assert status({"text": "x", "repeat": 1.5}) == 400
    assert status({"text": "x", "priority": "urgent"}) == 400
    assert status({"text": "x", "color": "red"}) == 400
    assert status({"text": "x", "speed": 500}) == 400
    assert status({"text": "x", "ttl": 0}) == 400
    assert status({"text": "x" * 501}) == 400
    assert status({"text": "x", "image_url": "ftp://a/b.png"}) == 400
    assert status({"text": "x", "data": {"a": 1}}) == 400


def test_color_and_priority(root):
    n = parse({"text": "x", "color": "#ff8000", "priority": "high", "duration": 2.5, "repeat": 3}, 0)
    assert n.color == (255, 128, 0) and n.high and n.duration == 2500 and n.repeat == 3


def test_image_limits_checked_before_decoding(root, monkeypatch):
    decoded = []
    monkeypatch.setattr(stubs.image, "load", staticmethod(lambda d: decoded.append(d)))
    # too many base64 bytes: refused from the string length alone
    assert status({"image": "A" * (_png.MAX_BYTES * 4 // 3 + 8)}) == 413
    assert status({"image": b64(png(2049, 7))}) == 413
    assert status({"image": b64(png(53, 65))}) == 413
    assert status({"image": b64(b"GIF89a not a png at all......")}) == 400
    assert status({"image": "!!!notbase64"}) == 400
    assert status({"image": b64(png(5, 5)), "image_url": "http://x/y.png"}) == 400
    assert decoded == []
    assert parse({"image": b64(png(2048, 64))}, 0).png[:8] == _png.SIG


def test_sound_fields(root):
    assert parse({"text": "x", "sound": "chime"}, 0).sound == "chime"
    assert status({"text": "x", "sound": "nope"}) == 400
    assert status({"text": "x", "sound": "../etc"}) == 400
    assert status({"text": "x", "sound": "chime", "sound_url": "http://a/b.wav"}) == 400
    assert status({"text": "x", "sound_data": b64(b"RIFF....nope")}) == 400
    assert status({"text": "x", "sound_data": b64(wav(20000))}) == 413
    n = parse({"text": "x", "sound_data": b64(wav(100))}, 0)
    assert n.sound_data[:4] == b"RIFF"


def test_plugin_forcing_fields(rig):
    make_plugin(rig.root, "one", "def update(): pass\n")
    make_plugin(rig.root, "off", "def update(): pass\n")
    set_plugins(rig.root, ["one", "off"], disabled_plugins=["off"])
    rt = rig.boot()
    rt.config.values["disabled_plugins"] = ["off"]
    n = parse({"plugin": "off", "data": {"n": 1}}, 0, loader=rt.loader, config=rt.config)
    assert n.plugin == "off" and n.data == {"n": 1}
    assert status({"plugin": "missing"}, loader=rt.loader, config=rt.config) == 400
    assert status({"plugin": "one", "text": "x"}, loader=rt.loader, config=rt.config) == 400
    rt.config.values["quarantined"] = {"one": "crashed"}
    assert status({"plugin": "one"}, loader=rt.loader, config=rt.config) == 409


# ---- queue --------------------------------------------------------------------

def note(nid, now=0, high=False, ttl=600):
    return parse({"text": nid, "id": nid, "priority": "high" if high else "normal", "ttl": ttl}, now)


def test_queue_priority_and_fifo():
    q = Queue()
    q.push(note("a", 0), 0)
    q.push(note("b", 10), 10)
    q.push(note("h", 20, high=True), 20)
    assert [q.pop(30).id for _ in range(3)] == ["h", "a", "b"]
    assert q.pop(30) is None


def test_queue_aging_lets_old_normal_notes_through():
    q = Queue()
    q.push(note("old", 0), 0)
    q.push(note("high", _notify.AGE_MS + 5, high=True), _notify.AGE_MS + 5)
    assert q.pop(_notify.AGE_MS + 10).id == "old"


def test_queue_ttl_expiry_and_dedupe():
    q = Queue()
    q.push(note("short", 0, ttl=1), 0)
    q.push(note("x", 0), 0)
    replacement = note("x", 5)
    replacement.text = "new"
    q.push(replacement, 5)
    assert len(q) == 2
    assert q.pop(2000).text == "new"
    assert q.pop(2000) is None


def test_queue_full_is_429():
    q = Queue()
    for i in range(_notify.QUEUE_SIZE):
        q.push(note(f"n{i}"), 0)
    with pytest.raises(NotifyError) as e:
        q.push(note("over"), 0)
    assert e.value.status == 429
    q.push(note("n3"), 0)   # same id replaces, even when full


# ---- quiet hours / sound policy ----------------------------------------------

class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def t(h, m, wd):
    return (2026, 1, 1, h, m, 0, wd, 1)


def test_quiet_hours_overnight_belongs_to_start_day():
    c = Cfg(quiet_hours_enabled=True, quiet_hours=("22:00", "07:00"), quiet_days=[4])   # Fridays
    assert _notify.in_quiet_hours(c, t(23, 0, 4))
    assert _notify.in_quiet_hours(c, t(6, 59, 5))      # Saturday morning, Friday's night
    assert not _notify.in_quiet_hours(c, t(7, 0, 5))
    assert not _notify.in_quiet_hours(c, t(23, 0, 5))
    assert not _notify.in_quiet_hours(c, t(3, 0, 4))   # Thursday's night


def test_quiet_hours_same_day_and_old_format():
    c = Cfg(quiet_hours_enabled=True, quiet_hours=("09:30", "17:00"))
    assert _notify.in_quiet_hours(c, t(9, 30, 0)) and not _notify.in_quiet_hours(c, t(17, 0, 0))
    old = Cfg(quiet_hours_enabled=True, quiet_hours=(22, 7))
    assert _notify.in_quiet_hours(old, t(23, 0, 2)) and not _notify.in_quiet_hours(old, t(12, 0, 2))
    assert not _notify.in_quiet_hours(Cfg(quiet_hours=(0, 0)), t(1, 0, 0))   # disabled


def test_sound_policy():
    quiet = Cfg(quiet_hours_enabled=True, quiet_hours=("00:00", "00:00"))
    assert _notify.sound_policy(Cfg(), False, t=t(12, 0, 0)) == "play"
    assert _notify.sound_policy(Cfg(alert_sound=False), True, t=t(12, 0, 0)) == "off"
    assert _notify.sound_policy(quiet, True, t=t(12, 0, 0)) == "quiet"
    assert _notify.sound_policy(Cfg(quiet, quiet_bypass_high=True), True, t=t(12, 0, 0)) == "play"
    assert _notify.sound_policy(Cfg(quiet, quiet_bypass_high=True), False, t=t(12, 0, 0)) == "quiet"
    assert _notify.sound_policy(Cfg(), True, True, t(12, 0, 0)) == "streaming"
    assert _notify.sound_policy(Cfg(alert_interrupt_audio=True), True, True, t(12, 0, 0)) == "interrupt"
    assert _notify.sound_policy(Cfg(alert_interrupt_audio=True), False, True, t(12, 0, 0)) == "streaming"


# ---- images and layout --------------------------------------------------------

def test_fitted_size_rules():
    assert _png.fitted_size(200, 7) == (200, 7)       # sideways scroll
    assert _png.fitted_size(53, 40) == (53, 40)       # vertical scroll
    assert _png.fitted_size(10, 5) == (10, 5)         # centred
    assert _png.fitted_size(106, 14) == (53, 7)       # both too big: 7 rows, aspect kept
    assert _png.fitted_size(400, 64) == (44, 7)


def test_decode_scales_to_seven_rows():
    img = _png.decode(stubs.make_png(106, 14, lambda x, y: (255, 0, 0, 255) if x < 53 else (0, 0, 255, 255)))
    assert (img.width, img.height) == (53, 7)
    assert img.pixel(0, 0) & 0xFF == 255 and img.pixel(52, 6) >> 16 & 0xFF == 255


def render(text=None, image=None, **body):
    if text:
        body["text"] = text
    if image is not None:
        body["image"] = b64(image)
    n = parse(body, 0)
    if n.png:
        n.image = _png.decode(n.png)
    target = stubs.image()
    return _notify.Renderer(n, target), target


def lit_columns(target):
    return sorted({x for x in range(53) for y in range(7) if target.pixel(x, y) & 0xFFFFFF})


def test_small_image_is_centred_still():
    r, target = render(image=png(5, 3))
    assert r.segments == [(_notify.IMG_STATIC, _notify.STATIC_MS)]
    r.draw(target, 100)
    assert lit_columns(target) == list(range(24, 29))
    assert r.draw(target, _notify.STATIC_MS)       # one pass then done


def test_wide_image_scrolls_sideways():
    r, target = render(image=png(100, 7), speed=50)
    kind, ms = r.segments[0]
    assert kind == _notify.IMG_HSCROLL and ms == (53 + 100) * 16  # 50 px/s rounds to 1 px a frame
    r.draw(target, 0)
    assert lit_columns(target) == []                # enters from the right
    r.draw(target, 160)
    assert lit_columns(target) == list(range(43, 53))


def test_tall_image_scrolls_up():
    r, target = render(image=png(53, 20), speed=10)
    kind, ms = r.segments[0]
    assert kind == _notify.IMG_VSCROLL and ms == (7 + 20) * 96  # 10 px/s: 6 frames a pixel
    r.draw(target, 300)                              # 3 px in: rows 4-6 lit
    assert [y for y in range(7) if target.pixel(0, y) & 0xFFFFFF] == [4, 5, 6]


def test_large_image_is_scaled_then_scrolled():
    r, _ = render(image=png(400, 64))
    assert (r.img.width, r.img.height) == (44, 7)
    assert r.segments[0][0] == _notify.IMG_STATIC


def test_icon_with_text_keeps_icon_at_left():
    r, target = render("a long message that will scroll", png(5, 5, (0, 255, 0, 255)))
    assert r.icon and r.x0 == 6 and len(r.segments) == 1 and r.scroll
    r.draw(target, 1500)
    for y in range(1, 6):
        for x in range(5):
            assert target.pixel(x, y) == 0xFF00FF00     # icon pixels, not text
    assert target.pixel(5, 3) & 0xFFFFFF == 0          # gap column cleared


def test_image_then_text_and_timing():
    r, target = render("hi", png(30, 7), repeat=2)
    assert [k for k, _ in r.segments] == [_notify.IMG_STATIC, _notify.TEXT]
    assert r.total_ms == 2 * 2 * _notify.STATIC_MS
    r.draw(target, _notify.STATIC_MS + 10)
    assert target.texts[-1][0] == "hi"
    r2, _ = render("hi", duration=1)
    assert r2.total_ms == 1000


def test_long_text_capped_at_sixty_seconds():
    r, _ = render("x" * 400, speed=5, repeat=5)
    assert r.total_ms == 60000


# ---- the host taking over -----------------------------------------------------

RECORDER = """
calls = []
def on_show(): calls.append("show")
def on_hide(): calls.append("hide")
def update(): pass
"""

FORCED = """
import gitmatrix
calls = []
seen = []
def on_show(): calls.append("show")
def on_hide(): calls.append("hide")
def on_notify(data): calls.append(("notify", data))
def update(): seen.append(gitmatrix.notification)
"""


def boot(rig, **extra):
    make_plugin(rig.root, "one", RECORDER)
    make_plugin(rig.root, "two", RECORDER)
    make_plugin(rig.root, "forced", FORCED)
    set_plugins(rig.root, ["one", "two", "forced"], disabled_plugins=["forced"], **extra)
    return rig.boot()


def send(rt, body):
    n = rt.notifier.submit(body)
    asyncio.run(rt.notifier.step())
    return n


def mod(name):
    import sys
    return sys.modules[f"plugins.{name}"]


def test_text_notification_preempts_and_restores(rig):
    rt = boot(rig)
    host = rt.host
    rig.frames(10)
    one = mod("one")
    shown_for = rig.clock.now - host.entered
    send(rt, {"text": "hi", "duration": 1})
    rig.frames()
    assert host.mode == _host.NOTIFY and host.note.text == "hi"
    assert one.calls[-1] == "hide"
    assert host.surface.texts[-1][0] == "hi"
    for _ in range(100):
        rig.frames()
        if host.mode == _host.PLUGIN:
            break
    assert host.current.name == "one" and one.calls[-1] == "show"
    # rotation timer resumed where it left off, not restarted
    assert rig.clock.now - host.entered == pytest.approx(shown_for + 16, abs=20)
    assert rt.notifier.shown == 1 and rt.notifier.active is None


def test_rotation_paused_while_notifying(rig):
    rt = boot(rig, delay=1)
    rig.frames(30)
    first = rt.host.current.name
    send(rt, {"text": "hold", "duration": 5})
    rig.frames(200)
    assert rt.host.mode == _host.NOTIFY
    assert rt.host.saved[0].name == first


def test_press_dismisses_then_next_press_opens_menu(rig):
    rt = boot(rig)
    rig.frames()
    send(rt, {"text": "one", "duration": 30})
    send(rt, {"text": "two", "duration": 30})
    rig.frames()
    assert rt.host.note.text == "one"
    rig.press()
    assert rt.host.mode == _host.PLUGIN and rt.host.note is None
    rig.frames(5)
    assert rt.host.mode == _host.PLUGIN      # grace: "two" waits
    rig.press()
    assert rt.host.mode == _host.MENU


def test_notifications_wait_while_menu_open(rig):
    rt = boot(rig)
    rig.press()
    assert rt.host.mode == _host.MENU
    send(rt, {"text": "later", "priority": "high"})
    rig.frames(20)
    assert rt.host.mode == _host.MENU and rt.notifier.pending is not None
    # a high-priority note blinks the top-right pixel
    lit = []
    for _ in range(40):
        rig.frames()
        lit.append(rt.compositor.fb.pixel(52, 0) == 0xFF0000FF)
    assert any(lit) and not all(lit)
    rt.host.menu.close()
    rt.host.menu_closed()
    rig.frames()
    assert rt.host.mode == _host.NOTIFY and rt.host.note.text == "later"


def test_queued_notes_chain_without_restoring(rig):
    rt = boot(rig)
    rig.frames()
    one = mod("one")
    send(rt, {"text": "a", "duration": 0.1})
    rig.frames()
    send(rt, {"text": "b", "duration": 0.1})
    hides = one.calls.count("hide")
    rig.frames(8)
    assert rt.host.note.text == "b"
    assert one.calls.count("show") == 1 and one.calls.count("hide") == hides
    rig.frames(10)
    assert rt.host.mode == _host.PLUGIN and one.calls[-1] == "show"


def test_same_id_replaces_active_note(rig):
    rt = boot(rig)
    rig.frames()
    send(rt, {"text": "v1", "id": "build", "duration": 0.1})
    rig.frames()
    rt.notifier.submit({"text": "v2", "id": "build", "duration": 1})
    rig.frames(20)                 # v1's time is up but v2 isn't ready: v1 stays
    assert rt.host.note.text == "v1"
    asyncio.run(rt.notifier.step())
    rig.frames()
    assert rt.host.note.text == "v2"


def test_api_dismiss(rig):
    rt = boot(rig)
    rig.frames()
    send(rt, {"text": "x", "duration": 30})
    rt.notifier.submit({"text": "queued"})
    rig.frames()
    assert rt.notifier.dismiss() == 2
    rig.frames()
    assert rt.host.mode == _host.PLUGIN and len(rt.notifier.queue) == 0


def test_forcing_a_disabled_plugin_with_data(rig):
    rt = boot(rig)
    rig.frames()
    one = mod("one")
    send(rt, {"plugin": "forced", "data": {"build": "green"}, "duration": 0.5})
    rig.frames()
    forced = mod("forced")
    assert rt.host.forced.name == "forced"
    assert forced.calls == ["show", ("notify", {"build": "green"})]
    rig.frames(5)
    assert forced.seen[-1] == {"build": "green"}
    assert "forced" not in [p.name for p in rt.loader.active()]   # still not in rotation
    rig.frames(40)
    assert rt.host.mode == _host.PLUGIN and rt.host.current.name == "one"
    assert forced.calls[-1] == "hide" and gitmatrix.notification is None
    assert one.calls[-1] == "show"
    assert "forced" not in rt.loader.plugins                      # unloaded again
    assert "forced" in rt.config.get("disabled_plugins")


def test_forced_plugin_quarantined_after_queueing(rig):
    rt = boot(rig)
    rig.frames()
    send(rt, {"plugin": "forced"})
    rt.config.values["quarantined"] = {"forced": "crashed"}
    rig.frames()
    assert rt.host.mode == _host.PLUGIN and rt.host.current.name == "one"
    assert "can't be shown" in rt.host.message[0]


def test_crashing_forced_plugin_ends_notification(rig):
    make_plugin(rig.root, "bad", "def update():\n    raise ValueError('boom')\n")
    rt = boot(rig)
    rt.config.values["plugins"].append("bad")
    rig.frames()
    send(rt, {"plugin": "bad", "duration": 30})
    rig.frames(12)
    assert rt.host.mode == _host.PLUGIN and rt.host.forced is None


def test_bad_image_url_is_dropped(rig, monkeypatch):
    rt = boot(rig)

    async def boom(url, limit):
        raise OSError("HTTP 404")

    monkeypatch.setattr(_notify, "_fetch", boom)
    send(rt, {"image_url": "https://example.com/x.png"})
    assert rt.notifier.pending is None and rt.notifier.dropped == 1
    assert "404" in rt.notifier.last_error


def test_image_url_prefetched_before_showing(rig, monkeypatch):
    rt = boot(rig)
    got = []

    async def fake(url, limit):
        got.append((url, limit))
        return png(60, 7)

    monkeypatch.setattr(_notify, "_fetch", fake)
    send(rt, {"image_url": "https://example.com/x.png", "sound_url": "http://example.com/s.wav"} | {"sound_url": None})
    assert got == [("https://example.com/x.png", _png.MAX_BYTES)]
    assert rt.notifier.pending.image.width == 60


def test_split_url():
    assert _notify.split_url("https://example.com/a/b.png?x=1") == (True, "example.com", 443, "/a/b.png?x=1")
    assert _notify.split_url("http://10.0.0.2:8080") == (False, "10.0.0.2", 8080, "/")


def test_sound_starts_with_the_note(rig, monkeypatch):
    rt = boot(rig)
    played = []

    async def fake_alert(source, high, config):
        played.append((source, high))

    monkeypatch.setattr(_sounds, "alert", fake_alert)

    async def go():
        send_note = rt.notifier.submit({"text": "x", "sound": "ping", "priority": "high"})
        await rt.notifier.step()
        rig.frames()
        await asyncio.sleep(0)
        return send_note

    asyncio.run(go())
    assert played and played[0][0].endswith("/sounds/ping.wav") and played[0][1] is True


def test_scroll_steps_evenly_despite_frame_jitter():
    import random

    from gitmatrix import _display

    rnd = random.Random(1)
    for speed in (_notify.SPEED, 10, 50):
        dwell, last, run = [], 0, 0
        for f in range(400):
            t = f * _display.FRAME_MS + rnd.randint(0, 6)  # late wake-ups, as on device
            px = _display.scroll_px(t, speed)
            assert px - last in (0, 1)
            if px != last:
                dwell.append(run)
                run = 0
            last, run = px, run + 1
        assert len(set(dwell[1:])) == 1, (speed, dwell)  # every pixel shows for the same frames
