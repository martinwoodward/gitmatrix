# WiFi: setup AP, joining, the 1-minute AP fallback, AP off once joined, scans.

import stubs

from gitmatrix import _log, _wifi
from gitmatrix._wifi import CONNECTED, CONNECTING, FALLBACK, FALLBACK_MS, SETUP, WiFi


class Cfg(dict):
    def get(self, key, default=None):
        v = super().get(key)
        return default if v is None else v

    def set(self, key, value):
        self[key] = value


def make(ssid=None, password=None, firmware=None, **cfg):
    net = stubs.FakeNetwork()
    dns = []
    w = WiFi(Cfg(wifi_ssid=ssid, wifi_password=password, **cfg), firmware, net, dns.append)
    w.dns_calls = dns
    return w, net


def test_no_network_starts_wpa2_setup_ap_with_captive_dns():
    w, net = make()
    w.start(0)
    assert w.mode == SETUP
    assert net.ap.on and not net.sta.on
    assert net.ap.settings == {"essid": "GitMatrix", "password": "monalisa", "security": net.ap.SEC_WPA2}
    assert w.dns_calls == ["192.168.4.1"]
    assert net.name == "gitmatrix"
    assert w.url() == "http://192.168.4.1"
    text = w.status_text()
    assert "GitMatrix" in text and "monalisa" in text and "http://192.168.4.1" in text
    assert w.tick(10 * FALLBACK_MS) == SETUP  # nothing to retry


def test_firmware_config_overrides_ap_and_bad_password_falls_back():
    w, net = make(firmware={"AP_SSID": "Desk", "AP_PASSWORD": "hunter22"})
    w.start(0)
    assert net.ap.settings["essid"] == "Desk" and net.ap.settings["password"] == "hunter22"
    w, net = make(firmware={"AP_PASSWORD": "mona"})  # WPA2 needs 8+
    w.start(0)
    assert net.ap.settings["password"] == "monalisa"


def test_joins_configured_network_with_ap_off():
    w, net = make("home", "secret123", wifi_hostname="desk")
    w.start(0)
    assert w.mode == CONNECTING
    assert net.sta.joins == [("home", "secret123")]
    assert not net.ap.on and w.dns_calls == []
    assert net.name == "desk"
    net.sta.up = True
    assert w.tick(500) == CONNECTED
    assert w.ip == "192.168.1.42"
    assert w.url() == "http://192.168.1.42"
    assert w.status_text() == "home - visit http://192.168.1.42 or http://desk.local"
    assert w.status()["mdns"] == "http://desk.local"


def test_interfaces_left_up_by_a_soft_reset_are_restarted():
    w, net = make("new", "secret123")
    net.sta.on = net.sta.up = True  # still joined to the old network
    net.ap.on = True                # setup AP from the last boot
    w.start(0)
    assert not net.ap.on
    assert net.sta.on and not net.sta.up  # rejoining, with the new hostname
    assert net.sta.joins == [("new", "secret123")]


def test_open_network_joins_without_password():
    w, net = make("cafe")
    w.start(0)
    assert net.sta.joins == [("cafe", None)]


def test_retries_then_ap_after_a_minute_then_ap_off_when_joined():
    w, net = make("home", "secret123")
    w.start(0)
    net.sta.code = net.sta.result = -2  # network not found
    t = 0
    while t < FALLBACK_MS - 500:
        t += 500
        assert w.tick(t) == CONNECTING
    assert not net.ap.on
    assert len(net.sta.joins) == 3  # at 0, 15s, 45s
    assert w.tick(FALLBACK_MS) == FALLBACK
    assert net.ap.on and w.dns_calls == ["192.168.4.1"]
    assert "network not found" in w.status_text() and "monalisa" in w.status_text()
    assert w.url() == "http://192.168.4.1"
    # STA keeps retrying alongside the AP
    joins = len(net.sta.joins)
    for _ in range(240):
        t += 500
        w.tick(t)
    assert len(net.sta.joins) > joins
    net.sta.up = True
    assert w.tick(t + 500) == CONNECTED
    assert not net.ap.on
    assert w.status()["ap"] == {"on": False}


def test_lost_connection_reconnects_and_falls_back_again():
    w, net = make("home", "secret123")
    w.start(0)
    net.sta.up = True
    w.tick(500)
    net.sta.up = False
    assert w.tick(10000) == CONNECTING
    assert w.ip is None
    assert net.sta.joins[-1] == ("home", "secret123") and net.sta.disconnects == 0
    w.tick(10000 + FALLBACK_MS)
    assert w.mode == FALLBACK and net.ap.on
    # DNS keeps running from the first time; it isn't started twice
    net.sta.up = True
    w.tick(20000 + FALLBACK_MS)
    net.sta.up = False
    w.tick(30000 + FALLBACK_MS)
    w.tick(30000 + 2 * FALLBACK_MS)
    assert w.mode == FALLBACK and w.dns_calls == ["192.168.4.1"]


def test_scan_dedupes_sorts_and_activates_sta_in_setup():
    w, net = make()
    w.start(0)
    net.sta.found = [
        (b"home", b"x", 6, -70, 3, False),
        (b"home", b"y", 11, -50, 3, False),
        (b"", b"z", 1, -40, 0, False),        # hidden
        (b"cafe", b"w", 1, -60, 0, False),
        (b"\xff\xfe", b"v", 1, -60, 0, False),  # not utf-8
    ]
    nets = w.scan()
    assert net.sta.on
    assert nets == [
        {"ssid": "home", "rssi": -50, "secure": True, "channel": 11},
        {"ssid": "cafe", "rssi": -60, "secure": False, "channel": 1},
    ]
    assert w.scanned_at is not None and not w.scanning


def test_scan_keeps_watchdog_fed_while_blocked_and_stops_after():
    w, net = make("home", "secret123")
    w.start(0)
    feeds = []
    seen = []
    net.sta.found = [(b"home", b"x", 6, -50, 3, False)]
    real = net.sta.scan

    def slow_scan():
        assert [t.period for t in stubs.Timer.live] == [_wifi.SCAN_FEED_MS]
        seen.append(len(feeds))
        out = real()             # fires the keepalive timer once, as a ~1 s scan would
        seen.append(len(feeds))
        return out

    net.sta.scan = slow_scan
    w.scan(lambda: feeds.append(1))
    assert seen[1] > seen[0] >= 1      # fed before, and during, the blocking call
    assert stubs.Timer.live == []      # keepalive stopped afterwards


def test_prescan_only_in_setup_mode_and_scans_rate_limited():
    w, net = make("home", "secret123")
    w.start(0)
    assert w.prescan() == [] and net.sta.scans == 0   # joining: nothing to provision
    w, net = make()
    w.start(0)
    net.sta.found = [(b"cafe", b"w", 1, -60, 0, False)]
    assert w.scan_wait_ms(0) == 0
    assert w.prescan()[0]["ssid"] == "cafe" and net.sta.scans == 1
    now = w.scanned_at
    assert w.scan_wait_ms(now + 1000) == _wifi.SCAN_COOLDOWN_MS - 1000
    assert w.scan_wait_ms(now + _wifi.SCAN_COOLDOWN_MS) == 0


def test_scan_failure_is_logged_not_raised():
    w, net = make()
    w.start(0)

    def boom():
        raise OSError(5)

    net.sta.scan = boom
    _log.clear()
    assert w.scan() == []
    assert not w.scanning
    assert any("scan failed" in text for _, text in _log.recent())


def test_no_network_module_is_unavailable():
    w = WiFi(Cfg(), network_=None)
    w.net = None
    assert w.start(0).mode == _wifi.UNAVAILABLE
    assert w.status_text() == "WiFi unavailable"


def test_valid_hostname():
    assert _wifi.valid_hostname("gitmatrix")
    assert _wifi.valid_hostname("desk-2")
    assert not _wifi.valid_hostname("-bad")
    assert not _wifi.valid_hostname("has space")
    assert not _wifi.valid_hostname("x" * 33)
    assert not _wifi.valid_hostname("")


def test_restart_applies_settings_in_place_and_brings_the_ap_back():
    w, net = make()
    w.start(0)
    assert w.mode == SETUP and net.ap.on
    w.restart(100)                         # still no network: the AP must come back up
    assert w.mode == SETUP and net.ap.on and w.ap_on
    w.config.set("wifi_ssid", "home")
    w.config.set("wifi_hostname", "desk")
    w.restart(200)
    assert w.mode == CONNECTING and not net.ap.on and net.sta.joins[-1] == ("home", None)
    assert net.name == "desk"
    net.sta.up = True
    assert w.tick(300) == CONNECTED
    w.config.set("wifi_ssid", None)        # forget
    w.restart(400)
    assert w.mode == SETUP and net.ap.on and not net.sta.on and w.ip is None
    assert w.dns_calls == ["192.168.4.1"]  # the captive DNS server is started once
