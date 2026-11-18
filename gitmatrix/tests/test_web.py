# The web UI's JSON API (/api/v1), token auth, static pages and the captive portal.

import asyncio
import json

import pytest
import stubs
from conftest import make_plugin, set_plugins

from gitmatrix import _boot, _host, _log, _runtime, _web, _wifi

GOOD = "def update():\n    pass\n"


@pytest.fixture
def web(rig, monkeypatch):
    """Booted runtime with WiFi joined to 'home' and the routes registered."""
    for name in ("one", "two", "three"):
        make_plugin(rig.root, name, GOOD, {"name": name.title()})
    set_plugins(rig.root, ["one", "two", "three"], wifi_ssid="home", wifi_password="secret123")
    rig.net = stubs.FakeNetwork()
    rt = rig.boot()
    rig.net.sta.up = True
    rt.wifi.tick(rig.clock.advance(500))
    _web.setup(rt)
    spawned = []
    monkeypatch.setattr(_web, "spawn", spawned.append)
    rig.spawned = spawned
    return rig


def call(method, path, body=None, headers=None, host="gitmatrix.local"):
    from phew.server import Request

    r = Request(method, path, "HTTP/1.1")
    r.headers = {"host": host, **(headers or {})}
    r.data = body if body is not None else {}
    resp = _web.dispatch(r)
    if resp.headers.get("Content-Type") == "application/json":
        resp.json = json.loads(resp.body)
    return resp


def ok(method, path, body=None, **kw):
    resp = call(method, path, body, **kw)
    assert resp.status in (200, 202), (resp.status, resp.body)
    return resp.json


def test_status(web):
    s = ok("GET", "/api/v1/status")
    assert s["now_showing"] == {"name": "one", "title": "One"}
    assert s["plugins"] == 3
    assert s["wifi"]["connected"] and s["wifi"]["ip"] == "192.168.1.42"
    assert s["bluetooth"]["name"] == "GitMatrix"
    assert s["token_required"] is False
    assert s["input"]["seq"] == 0
    web.press()
    s = ok("GET", "/api/v1/status")
    assert s["menu_open"] and s["input"]["last"]["kind"] == "release"


def test_brightness_and_volume(web, bt):
    assert ok("POST", "/api/v1/brightness", {"value": 30}) == {"value": 30}
    assert web.rt.config.get("brightness") == 30
    assert ok("GET", "/api/v1/brightness") == {"value": 30}
    assert ok("POST", "/api/v1/volume", {"value": 100}) == {"value": 100}
    assert web.rt.config.get("volume") == 127 and bt.vol == 127
    assert call("POST", "/api/v1/volume", {"value": 101}).status == 400
    assert call("POST", "/api/v1/volume", {}).status == 400


def test_plugins_show_next_prev(web):
    p = ok("GET", "/api/v1/plugins")
    assert [x["name"] for x in p["plugins"]][:3] == ["one", "two", "three"]
    assert p["plugins"][0]["current"] and p["plugins"][0]["enabled"]
    assert not p["plugins"][3]["enabled"]  # built-ins arrive switched off
    assert ok("POST", "/api/v1/plugins/three/show")["now_showing"]["name"] == "three"
    assert web.rt.host.current.name == "three"
    assert ok("POST", "/api/v1/plugins/next")["now_showing"]["name"] == "one"
    assert ok("POST", "/api/v1/plugins/prev")["now_showing"]["name"] == "three"
    assert ok("GET", "/api/v1/plugins/two")["title"] == "Two"
    assert call("POST", "/api/v1/plugins/nope/show").status == 404
    assert call("GET", "/api/v1/plugins/nope").status == 404


def test_disable_enable_and_order(web):
    ok("POST", "/api/v1/plugins/one/disable")
    host = web.rt.host
    assert "one" in web.rt.config.get("disabled_plugins")
    assert host.current.name != "one"
    assert [p.name for p in host.plugins()] == ["two", "three"]
    info = ok("POST", "/api/v1/plugins/one/enable")
    assert info["enabled"] and info["loaded"]
    names = web.rt.config.get("plugins")
    p = ok("POST", "/api/v1/plugins/order", {"order": names[::-1]})
    assert [x["name"] for x in p["plugins"]] == names[::-1]
    assert call("POST", "/api/v1/plugins/order", {"order": names[1:]}).status == 400
    assert call("POST", "/api/v1/plugins/order", {"order": "one"}).status == 400


def test_unquarantine(web):
    web.rt.config.set("quarantined", {"two": "update: boom"})
    assert call("POST", "/api/v1/plugins/two/enable").status == 409
    assert ok("GET", "/api/v1/system")["quarantined"] == {"two": "update: boom"}
    info = ok("POST", "/api/v1/plugins/two/unquarantine")
    assert not info["quarantined"] and info["enabled"]
    assert call("POST", "/api/v1/plugins/two/unquarantine").status == 409


def test_settings_saves_config(web):
    s = ok("POST", "/api/v1/settings", {"delay": 12, "hostname": "Desk", "time_zone": "pacific", "brightness": 40})
    assert s["reboot_required"] and s["hostname"] == "desk" and s["delay"] == 12
    text = (web.root / "config.py").read_text()
    assert "wifi_hostname = 'desk'" in text and "delay = 12" in text and "time_zone = 'pacific'" in text
    assert any(z["key"] == "uk" for z in s["time_zones"])
    assert call("POST", "/api/v1/settings", {"hostname": "bad name"}).status == 400
    assert call("POST", "/api/v1/settings", {"time_zone": "mars"}).status == 400
    assert call("POST", "/api/v1/settings", {"delay": -1}).status == 400
    assert "reboot_required" not in ok("POST", "/api/v1/settings", {"hostname": "desk"})


def test_wifi_save_writes_config_and_reboots(web):
    r = ok("POST", "/api/v1/wifi", {"ssid": "office", "password": "letmein99"})
    assert r == {"ok": True, "rebooting": True, "ssid": "office", "url": "http://gitmatrix.local"}
    text = (web.root / "config.py").read_text()
    assert "wifi_ssid = 'office'" in text and "wifi_password = 'letmein99'" in text
    assert _runtime.reboot_at is not None
    _boot.housekeeping(web.rt, web.clock.advance(100))  # response still going out
    with pytest.raises(SystemExit, match="reboot"):
        _boot.housekeeping(web.rt, web.clock.advance(2000))


def test_wifi_save_validation_and_password_keeping(web):
    assert call("POST", "/api/v1/wifi", {"ssid": ""}).status == 400
    assert call("POST", "/api/v1/wifi", {"ssid": "x" * 33}).status == 400
    assert call("POST", "/api/v1/wifi", {"ssid": "home", "password": "short"}).status == 400
    assert _runtime.reboot_at is None
    ok("POST", "/api/v1/wifi", {"ssid": "home"})           # same network: keep the password
    assert web.rt.config.get("wifi_password") == "secret123"
    ok("POST", "/api/v1/wifi", {"ssid": "cafe", "password": ""})  # open network
    assert web.rt.config.get("wifi_password") is None
    ok("POST", "/api/v1/wifi/forget")
    assert web.rt.config.get("wifi_ssid") is None


def test_wifi_scan_runs_in_background(web, bt):
    web.net.sta.found = [(b"office", b"m", 6, -40, 3, False)]
    bt.streaming_now = True
    r = call("POST", "/api/v1/wifi/scan")
    assert r.status == 202 and r.json == {"scanning": True, "warning": _web.SCAN_WARNING}
    assert ok("GET", "/api/v1/wifi/networks")["scanning"]
    assert call("POST", "/api/v1/wifi/scan").status == 202
    assert len(web.spawned) == 1  # a second click doesn't start a second scan
    feeds = []
    web.rt.watchdog.feed = lambda: feeds.append(len(stubs.Timer.live))
    asyncio.run(web.spawned[0])
    n = ok("GET", "/api/v1/wifi/networks")
    assert not n["scanning"] and n["networks"][0]["ssid"] == "office" and n["current"] == "home"
    assert 1 in feeds and stubs.Timer.live == []  # fed by the keepalive timer mid-scan, then stopped
    w = ok("GET", "/api/v1/wifi")
    assert w["audio_streaming"] and w["mode"] == _wifi.CONNECTED


def test_wifi_rescans_are_rate_limited(web):
    assert call("POST", "/api/v1/wifi/scan").status == 202
    asyncio.run(web.spawned[0])
    web.clock.advance(1000)
    r = call("POST", "/api/v1/wifi/scan")
    assert r.status == 429 and r.json["retry_ms"] == _wifi.SCAN_COOLDOWN_MS - 1000
    assert "try again in 14 s" in r.json["error"]
    assert len(web.spawned) == 1 and web.net.sta.scans == 1
    web.clock.advance(_wifi.SCAN_COOLDOWN_MS)
    assert call("POST", "/api/v1/wifi/scan").status == 202 and len(web.spawned) == 2
    web.spawned[1].close()


def test_bluetooth(web, bt):
    b = ok("GET", "/api/v1/bluetooth")
    assert b["pairable"] and b["pairable_supported"]
    assert bt.pairable is True  # applied at boot
    assert not ok("POST", "/api/v1/bluetooth/pairable", {"value": False})["pairable"]
    assert bt.pairable is False and web.rt.config.get("bt_pairable") is False
    (web.root / "bt_link_keys.bin").write_bytes(b"k")
    ok("POST", "/api/v1/bluetooth/forget")
    assert not (web.root / "bt_link_keys.bin").exists()


def test_system_logs_and_reboot(web):
    s = ok("GET", "/api/v1/system")
    assert s["version"] and s["flash"]["total"] > 0 and not s["reboot_pending"]
    _log.log("hello from the test")
    assert ok("GET", "/api/v1/system/logs")["lines"][-1]["text"] == "hello from the test"
    ok("POST", "/api/v1/system/reboot")
    assert ok("GET", "/api/v1/system")["reboot_pending"]


def test_token_auth(web):
    s = ok("POST", "/api/v1/settings", {"api_token": "s3cret"})
    assert s["token_set"]
    resp = call("POST", "/api/v1/settings", {"api_token": "s3cret"}, headers={"authorization": "Bearer s3cret"})
    assert "gm_token=s3cret" in resp.headers["Set-Cookie"] and "HttpOnly" in resp.headers["Set-Cookie"]
    assert call("GET", "/api/v1/status").status == 401
    assert call("GET", "/api/v1/status", headers={"authorization": "Bearer nope"}).status == 401
    assert call("GET", "/api/v1/status", headers={"authorization": "Bearer s3cret"}).status == 200
    jar = {"cookie": "other=1; gm_token=s3cret"}
    assert call("GET", "/api/v1/status", headers=jar).status == 200
    assert call("POST", "/api/v1/plugins/next", headers={**jar, "origin": "http://gitmatrix.local"}).status == 200
    assert call("POST", "/api/v1/plugins/next", headers={**jar, "origin": "http://evil.example"}).status == 401
    # pages stay open and hand the browser its cookie
    page = call("GET", "/")
    assert page.status == 200 and "gm_token=s3cret" in page.headers["Set-Cookie"]
    # clearing it opens the API again
    ok("POST", "/api/v1/settings", {"api_token": ""}, headers={"authorization": "Bearer s3cret"})
    assert call("GET", "/api/v1/status").status == 200
    assert call("POST", "/api/v1/settings", {"api_token": "has space"}).status == 400


def test_static_pages(web):
    for path in ("/", "/index.html", "/wifi", "/settings.html", "/bluetooth.html", "/system.html",
                 "/discover.html", "/plugin.html", "/notifications.html", "/sounds.html", "/static/app.js"):
        resp = call("GET", path)
        assert resp.status == 200, path
    assert call("GET", "/static/app.css").headers["Content-Type"] == "text/css"
    assert call("GET", "/").headers["Cache-Control"] == "no-cache"
    assert call("GET", "/nope.html").status == 404
    assert call("GET", "/../config.py").status == 404
    assert call("GET", "/static/../../config.py").status == 404
    r = call("GET", "/api/v1/nope")
    assert r.status == 404 and r.json == {"error": "no such endpoint"}
    assert call("GET", "/api/v1/wifi/scan").status == 404  # wrong method


def test_captive_portal_redirects_unknown_hosts_in_ap_mode(rig):
    set_plugins(rig.root, [])
    rig.net = stubs.FakeNetwork()
    rt = rig.boot()
    assert rt.wifi.mode == _wifi.SETUP and rig.net.ap.on
    # scanned once during boot, before the event loop, for the setup page
    assert rig.net.sta.scans == 1 and rt.wifi.scanned_at is not None
    _web.setup(rt)
    r = call("GET", "/generate_204", host="connectivitycheck.gstatic.com")
    assert r.status == 302 and r.headers["Location"] == "http://192.168.4.1/wifi.html"
    assert call("GET", "/wifi.html", host="192.168.4.1").status == 200
    assert ok("GET", "/api/v1/wifi", host="192.168.4.1")["ap"]["ssid"] == "GitMatrix"


def test_firmware_config_created_with_defaults(rig):
    set_plugins(rig.root, [])
    rig.boot()
    text = (rig.root / "gitmatrix_config.py").read_text()
    assert 'AP_PASSWORD = "monalisa"' in text
    assert _runtime.firmware["PLUGIN_REPO"] == "martinwoodward/gitmatrix-plugins"
    assert _runtime.firmware["PLUGIN_BRANCH"] == "main"
    (rig.root / "gitmatrix_config.py").write_text('AP_PASSWORD = "hunter22"\n')
    rig.net = stubs.FakeNetwork()
    rig.boot()
    assert _runtime.firmware["AP_PASSWORD"] == "hunter22" and _runtime.firmware["AP_SSID"] == "GitMatrix"
    assert rig.net.ap.settings["password"] == "hunter22"


def test_wifi_failure_does_not_stop_boot(rig):
    set_plugins(rig.root, [], wifi_ssid="home")
    rig.net = stubs.FakeNetwork()

    def broken(name=None):
        raise RuntimeError("radio on fire")

    rig.net.WLAN = broken
    rt = rig.boot()
    assert rt.host is not None and isinstance(rt.host, _host.Host)


def test_wifi_save_while_streaming_pauses_audio_and_warns(web, bt):
    bt.streaming_now = True
    r = ok("POST", "/api/v1/wifi", {"ssid": "office", "password": "letmein99"})
    assert "reconnect your device" in r["warning"]
    assert "wifi_ssid = 'office'" in (web.root / "config.py").read_text()
    assert bt.deinits == 1 and bt.name == "GitMatrix"


def test_device_settings_wait_for_playback_to_stop(web, bt):
    bt.streaming_now = True
    ok("POST", "/api/v1/settings", {"delay": 17})
    assert "delay = 17" not in (web.root / "config.py").read_text()
    assert bt.deinits == 0
    bt.streaming_now = False
    _boot.housekeeping(web.rt, web.clock.advance(1000))
    assert "delay = 17" in (web.root / "config.py").read_text()


# ---- notifications and sounds (phase 4) ---------------------------------------

def _b64(data):
    import base64
    return base64.b64encode(data).decode()


def _wav(frames=100):
    import struct
    pcm = struct.pack(f"<{frames}h", *([500] * frames))
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_notify_accepts_and_queues(web):
    out = ok("POST", "/api/v1/notify", {"text": "build passed", "id": "ci", "sound": "success"})
    assert out["id"] == "ci"
    status = ok("GET", "/api/v1/notify")
    assert [n["id"] for n in status["queued"]] == ["ci"]
    assert ok("GET", "/api/v1/status")["notifications"]["queued"][0]["id"] == "ci"


def test_notify_errors(web):
    assert call("POST", "/api/v1/notify", {}).status == 400
    assert call("POST", "/api/v1/notify", {"text": "x", "repeat": 9}).status == 400
    assert call("POST", "/api/v1/notify", {"image": "A" * 50000}).status == 413
    for i in range(8):
        ok("POST", "/api/v1/notify", {"text": str(i)})
    resp = call("POST", "/api/v1/notify", {"text": "full"})
    assert resp.status == 429 and "full" in resp.json["error"]


def test_notify_dismiss(web):
    ok("POST", "/api/v1/notify", {"text": "a", "id": "a"})
    ok("POST", "/api/v1/notify", {"text": "b", "id": "b"})
    assert ok("DELETE", "/api/v1/notify?id=a")["dismissed"] == 1
    assert ok("DELETE", "/api/v1/notify")["dismissed"] == 1
    assert ok("GET", "/api/v1/notify")["queued"] == []


def test_notify_needs_token_when_set(web):
    web.rt.config.set("api_token", "s3cret")
    assert call("POST", "/api/v1/notify", {"text": "x"}).status == 401
    resp = call("POST", "/api/v1/notify", {"text": "x"}, {"authorization": "Bearer s3cret"})
    assert resp.status == 202


def test_sounds_upload_play_delete(web):
    names = [s["name"] for s in ok("GET", "/api/v1/sounds")["sounds"]]
    assert names[:2] == ["chime", "ping"]
    from phew.server import Request
    r = Request("POST", "/api/v1/sounds/bell", "HTTP/1.1")
    r.headers = {"host": "gitmatrix.local", "content-type": "audio/wav"}
    r.body = _wav()
    assert _web.dispatch(r).status == 200
    assert ok("POST", "/api/v1/sounds/horn", {"data": _b64(_wav())})["saved"]
    assert {"bell", "horn"} <= {s["name"] for s in ok("GET", "/api/v1/sounds")["sounds"]}
    assert ok("POST", "/api/v1/sounds/bell/play")["played"]
    assert len(web.spawned) == 1
    web.spawned.pop().close()
    assert ok("POST", "/api/v1/notify", {"text": "x", "sound": "bell"})
    assert ok("DELETE", "/api/v1/sounds/bell")["deleted"] == "bell"
    assert call("DELETE", "/api/v1/sounds/bell").status == 404
    assert call("DELETE", "/api/v1/sounds/chime").status == 400
    assert call("POST", "/api/v1/sounds/chime", {"data": _b64(_wav())}).status == 400
    assert call("POST", "/api/v1/sounds/x", {"data": _b64(b"nope")}).status == 400


def test_sound_upload_while_streaming_is_queued_not_refused(web, bt):
    bt.streaming_now = True
    resp = call("POST", "/api/v1/sounds/later", {"data": _b64(_wav())})
    assert resp.status == 202 and resp.json["queued"]
    assert ok("GET", "/api/v1/sounds")["pending"] == ["later"]
    assert not ok("POST", "/api/v1/sounds/chime/play")["played"]
    bt.streaming_now = False
    _boot.housekeeping(web.rt, web.clock.advance(1000))
    assert "later" in {s["name"] for s in ok("GET", "/api/v1/sounds")["sounds"]}


def test_alert_settings(web):
    out = ok("POST", "/api/v1/settings", {"alert_sound": False, "quiet_hours_enabled": "on",
                                          "quiet_start": "21:30", "quiet_end": "6:45",
                                          "quiet_days": "0,1,2,3,4", "quiet_bypass_high": True})
    assert out["alert_sound"] is False and out["quiet_hours_enabled"] and out["quiet_bypass_high"]
    assert (out["quiet_start"], out["quiet_end"], out["quiet_days"]) == ("21:30", "06:45", [0, 1, 2, 3, 4])
    assert out["alert_interrupt_audio"] is False
    cfg = web.rt.config
    assert cfg.get("quiet_hours") == ("21:30", "06:45")
    assert call("POST", "/api/v1/settings", {"quiet_start": "25:00"}).status == 400
    assert call("POST", "/api/v1/settings", {"quiet_days": [7]}).status == 400


def test_body_limit_checked_before_reading(web):
    from phew import server
    from phew.server import Request

    class Reader:
        read_any = False

        async def readexactly(self, n):
            Reader.read_any = True
            return b"{}"

    def req(path, ctype, length):
        r = Request("POST", path, "HTTP/1.1")
        r.headers = {"content-type": ctype, "content-length": str(length)}
        return r

    with pytest.raises(server.BodyTooLarge):
        asyncio.run(server._read_request_body(Reader(), req("/api/v1/notify", "application/json", 70000)))  # noqa: SLF001
    assert not Reader.read_any
    big_wav = req("/api/v1/sounds/x", "audio/wav", 200000)
    asyncio.run(server._read_request_body(Reader(), big_wav))  # noqa: SLF001
    assert big_wav.body == b"{}"
    with pytest.raises(server.BodyTooLarge):
        asyncio.run(server._read_request_body(Reader(), req("/api/v1/sounds/x", "application/json", 70000)))  # noqa: SLF001


# ---- REST API v1 complete (phase 5) --------------------------------------------

from gitmatrix import _events, _notify, _sounds  # noqa: E402


@pytest.fixture
def fast_poll(monkeypatch):
    async def tick(_ms):
        await asyncio.sleep(0)

    monkeypatch.setattr(_events, "sleep_ms", tick)


def get(path, **kw):
    """call(), running a deferred (long-poll) reply to completion."""
    from phew.server import Request

    r = Request("GET", path, "HTTP/1.1")
    r.headers = {"host": "gitmatrix.local", **kw.get("headers", {})}
    resp = _web.dispatch(r)
    if type(resp).__name__ == "Deferred":
        resp = asyncio.run(resp.coro)
    resp.json = json.loads(resp.body)
    return resp


def types(out):
    return [e["type"] for e in out["events"]]


def test_status_phase5_fields(web):
    web.frames()
    s = ok("GET", "/api/v1/status")
    assert s["plugin"] == "one" and s["mode"] == "plugin" and s["firmware"] == s["version"]
    assert s["encoder"] == {"position": 0, "seq": 0, "last": None}
    assert s["events"]["seq"] > 0 and s["events"]["pollers"] == 0
    assert "memory" in s and "free" in s["flash"]
    assert s["notifications"]["active"] is None
    web.turn(2)
    web.turn(-1)
    s = ok("GET", "/api/v1/status")
    assert s["encoder"]["position"] == 1 and s["encoder"]["seq"] == 2
    assert s["encoder"]["last"] == {"action": "turn", "delta": -1, "position": 1}
    web.press()
    assert ok("GET", "/api/v1/status")["mode"] == "menu"


def test_events_history_knob_and_state_changes(web, bt):
    web.frames()
    out = get("/api/v1/events").json
    assert {"plugin", "mode", "wifi", "bluetooth", "brightness", "volume"} <= set(types(out))
    assert out["dropped"] == 0 and not out["reset"]
    since = out["next"]
    web.turn(1)
    web.frames()        # the plugin change is published on the next frame
    out = get(f"/api/v1/events?since={since}&timeout=0").json
    turn = out["events"][0]
    assert turn["type"] == "encoder" and turn["action"] == "turn" and turn["delta"] == 1 and turn["position"] == 1
    # observed, not consumed: the knob still moved to the next plugin
    plugin = [e for e in out["events"] if e["type"] == "plugin"]
    assert web.rt.host.current.name == "two" and plugin[-1]["name"] == "two" and plugin[-1]["previous"] == "one"
    since = out["next"]
    ok("POST", "/api/v1/volume", {"value": 80})
    ok("POST", "/api/v1/brightness", {"value": 20})
    ok("POST", "/api/v1/brightness", {"value": 20})     # no change, no event
    bt.is_connected = bt.streaming_now = True
    web.frames(40)
    out = get(f"/api/v1/events?since={since}&timeout=0").json
    assert types(out) == ["volume", "brightness", "bluetooth"]
    assert out["events"][0]["value"] == 80 and out["events"][2]["streaming"]
    only = get(f"/api/v1/events?since={since}&types=brightness&timeout=0").json
    assert types(only) == ["brightness"] and only["next"] == out["next"]


def test_events_notification_start_and_end(web):
    web.frames()
    since = web.rt.events.seq
    ok("POST", "/api/v1/notify", {"text": "hi", "id": "n", "duration": 0.1})
    asyncio.run(web.rt.notifier.step())
    web.frames(20)
    out = get(f"/api/v1/events?since={since}&timeout=0").json
    notes = [(e["action"], e["id"]) for e in out["events"] if e["type"] == "notification"]
    assert notes == [("start", "n"), ("end", "n")]
    modes = [e["mode"] for e in out["events"] if e["type"] == "mode"]
    assert modes == ["notification", "plugin"]


def test_events_long_poll(web, fast_poll):
    bus = web.rt.events
    since = bus.seq
    out = get(f"/api/v1/events?since={since}&timeout=1")
    assert out.status == 200 and out.json["events"] == [] and out.json["next"] == since

    async def go():
        resp = call_raw(f"/api/v1/events?since={since}&timeout=30")
        task = asyncio.create_task(resp.coro)
        await asyncio.sleep(0)
        assert bus.waiters == 1
        bus.change("volume", 7)
        return await task

    resp = asyncio.run(go())
    out = json.loads(resp.body)
    assert [e["value"] for e in out["events"]] == [7] and out["next"] == since + 1
    assert bus.waiters == 0


def call_raw(path):
    from phew.server import Request

    r = Request("GET", path, "HTTP/1.1")
    r.headers = {"host": "gitmatrix.local"}
    return _web.dispatch(r)


def test_events_long_poll_cap_timeout_clamp_and_errors(web, fast_poll, monkeypatch):
    bus = web.rt.events
    since = bus.seq
    asked = []
    wait = bus.wait

    def spy(s, kinds=None, timeout_ms=0):
        asked.append(timeout_ms)
        return wait(s, kinds, timeout_ms)

    monkeypatch.setattr(bus, "wait", spy)

    async def go():
        a = asyncio.create_task(call_raw(f"/api/v1/events?since={since}&timeout=999").coro)
        b = asyncio.create_task(call_raw(f"/api/v1/events?since={since}").coro)
        await asyncio.sleep(0)
        assert bus.waiters == 2
        third = call("GET", f"/api/v1/events?since={since}")
        assert third.status == 429 and third.json["retry_ms"] > 0
        # history and immediate answers don't need a slot
        assert call("GET", "/api/v1/events").status == 200
        assert call("GET", f"/api/v1/events?since={since}&timeout=0").status == 200
        bus.emit("wifi")
        return await a, await b

    for resp in asyncio.run(go()):
        assert resp.status == 200
    assert asked == [30000, 25000]
    assert call("GET", "/api/v1/events?since=x").status == 400
    assert call("GET", "/api/v1/events?types=nope").status == 400


def test_show_with_data_and_duration_uses_notification_path(web):
    web.frames()
    out = ok("POST", "/api/v1/plugins/two/show", {"data": {"n": 1}, "duration": 0.2})
    assert out["temporary"] and out["queued"] == 1
    asyncio.run(web.rt.notifier.step())
    web.frames()
    host = web.rt.host
    assert host.mode == _host.NOTIFY and host.note.plugin == "two" and host.note.data == {"n": 1}
    web.frames(30)
    assert host.mode == _host.PLUGIN and host.current.name == "one"
    assert call("POST", "/api/v1/plugins/two/show", {"duration": 999}).status == 400
    assert call("POST", "/api/v1/plugins/nope/show", {"duration": 1}).status == 404
    assert call("POST", "/api/v1/plugins/two/show", {"duration": 1, "priority": "meh"}).status == 400


def test_sound_plays_under_alert_policy(web, bt, monkeypatch):
    out = call("POST", "/api/v1/sound", {"sound": "chime"})
    assert out.status == 202 and out.json == {"played": True, "result": "play"}
    web.spawned.pop().close()
    monkeypatch.setattr(_sounds, "playing", "chime")
    assert ok("POST", "/api/v1/sound", {"sound": "ping"})["result"] == "busy"
    monkeypatch.setattr(_sounds, "playing", None)
    web.rt.config.set("alert_sound", False)
    assert ok("POST", "/api/v1/sound", {"sound": "chime"}) == {"played": False, "result": "off"}
    assert ok("POST", "/api/v1/sound", {"sound": "chime", "force": True})["played"]
    web.spawned.pop().close()
    web.rt.config.set("alert_sound", True)
    monkeypatch.setattr(_notify, "in_quiet_hours", lambda config, t=None: True)
    assert ok("POST", "/api/v1/sound", {"sound": "chime"})["result"] == "quiet"
    monkeypatch.setattr(_notify, "in_quiet_hours", lambda config, t=None: False)
    # force never talks over a Bluetooth stream; only the interrupt setting does
    bt.streaming_now = True
    assert ok("POST", "/api/v1/sound", {"sound": "chime", "force": True})["result"] == "streaming"
    web.rt.config.set("alert_interrupt_audio", True)
    assert ok("POST", "/api/v1/sound", {"sound": "chime", "priority": "high"})["result"] == "interrupt"
    web.spawned.pop().close()
    assert not web.spawned


def test_sound_validation_and_stop(web, monkeypatch):
    assert call("POST", "/api/v1/sound", {}).status == 400
    assert call("POST", "/api/v1/sound", {"sound": "chime", "sound_url": "http://x/y.wav"}).status == 400
    assert call("POST", "/api/v1/sound", {"sound": "nope"}).status == 404
    assert call("POST", "/api/v1/sound", {"sound_url": "ftp://x"}).status == 400
    assert call("POST", "/api/v1/sound", {"sound_data": _b64(b"nope")}).status == 400
    assert call("POST", "/api/v1/sound", {"sound": "chime", "volume": 3}).status == 400
    assert call("POST", "/api/v1/sound", {"sound": "chime", "priority": "loud"}).status == 400
    assert call("POST", "/api/v1/sound", {"sound_data": _b64(_wav())}).status == 202
    web.spawned.pop().close()

    class Task:
        cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    task = Task()
    monkeypatch.setattr(_web, "spawn", lambda coro: (coro.close(), task)[1])
    assert ok("POST", "/api/v1/sound", {"sound_url": "http://example.com/a.wav"})["played"]
    assert ok("POST", "/api/v1/sound", {"sound": "chime"})["result"] == "busy"
    assert ok("DELETE", "/api/v1/sound") == {"stopped": True} and task.cancelled
    assert ok("DELETE", "/api/v1/sound") == {"stopped": False}


def test_sound_url_fetch_and_force_reach_alert(web, monkeypatch):
    calls = []

    async def fake_alert(source, high, config, force=False):
        calls.append((source, high, force))

    async def fake_fetch(url, limit):
        if "bad" in url:
            raise OSError("HTTP 404")
        return _wav()

    monkeypatch.setattr(_sounds, "alert", fake_alert)
    monkeypatch.setattr(_notify, "_fetch", fake_fetch)
    asyncio.run(_web._play_sound(None, "http://x/good.wav", True, True))
    assert calls[0][0] == _wav() and calls[0][1:] == (True, True)
    asyncio.run(_web._play_sound(None, "http://x/bad.wav", False, False))
    assert len(calls) == 1 and "404" in _sounds.last_result


def test_every_api_route_needs_the_token(web):
    web.rt.config.set("api_token", "s3cret")
    checked = 0
    for path, _handler, methods in _web._ROUTES:
        if not path.startswith("/api/v1/"):
            continue
        for method in methods:
            resp = call(method, path.replace("<name>", "one"))
            assert resp.status == 401, (method, path, resp.status)
            checked += 1
    assert checked > 40
    assert call("GET", "/api/v1/events", headers={"authorization": "Bearer s3cret"}).status == 200


def test_phew_serves_a_deferred_long_poll(web, fast_poll, monkeypatch):
    """Through phew's real request handler: the long-poll reply goes out once
    an event arrives."""
    from phew import server

    async def wait_for_ms(coro, ms):
        return await coro

    monkeypatch.setattr(server.uasyncio, "wait_for_ms", wait_for_ms, raising=False)
    bus = web.rt.events
    since = bus.seq

    class Reader:
        def __init__(self, lines):
            self.lines = list(lines)

        async def readline(self):
            return self.lines.pop(0) if self.lines else b"\r\n"

    class Writer:
        out = b""

        def write(self, data):
            Writer.out += bytes(data)

        async def drain(self):
            pass

    head = [f"GET /api/v1/events?since={since}&timeout=30 HTTP/1.1\r\n".encode(), b"Host: gitmatrix.local\r\n", b"\r\n"]

    async def go():
        task = asyncio.create_task(server._serve_request(Reader(head), Writer()))  # noqa: SLF001
        for _ in range(5):
            await asyncio.sleep(0)
        assert Writer.out == b"" and bus.waiters == 1
        bus.change("brightness", 1)
        await task

    asyncio.run(go())
    status, _, body = Writer.out.partition(b"\r\n\r\n")
    assert status.startswith(b"HTTP/1.1 200")
    assert [e["type"] for e in json.loads(body)["events"]] == ["brightness"]
