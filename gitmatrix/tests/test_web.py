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


def test_wifi_save_writes_config_and_reboots_via_watchdog(web):
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
    web.rt.watchdog.feed = lambda: feeds.append(1)
    asyncio.run(web.spawned[0])
    n = ok("GET", "/api/v1/wifi/networks")
    assert not n["scanning"] and n["networks"][0]["ssid"] == "office" and n["current"] == "home"
    assert feeds  # fed either side of the blocking scan
    w = ok("GET", "/api/v1/wifi")
    assert w["audio_streaming"] and w["mode"] == _wifi.CONNECTED


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
                 "/discover.html", "/notifications.html", "/sounds.html", "/static/app.js"):
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
