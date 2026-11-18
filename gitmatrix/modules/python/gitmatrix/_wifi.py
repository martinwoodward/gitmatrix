# WiFi: join the network in /config.py, or run the 'GitMatrix' access point
# (WPA2, password from /gitmatrix_config.py) with a captive DNS so a phone can
# reach the setup pages.
#
#   SETUP       no network configured: AP only
#   CONNECTING  joining the configured network (STA), AP off
#   FALLBACK    still not joined after FALLBACK_MS: AP up too, STA keeps retrying
#   CONNECTED   joined; the AP is switched off
#
# Everything here is non-blocking (wlan.connect() returns at once) and driven by
# tick() from a small asyncio task, so the display and Bluetooth audio carry on.
#
# The exception is scan(). MicroPython's CYW43 WLAN.scan() is one C call that
# spins until the radio finishes (~1-3 s, capped at 10 s); it has no async or
# per-channel form, and core1 belongs to bt_audio so it can't go on a thread.
# So it is kept off the event loop where possible and bounded where not:
#   * setup mode scans once at boot, before the loop starts (prescan()), so the
#     provisioning page has networks without stalling anything;
#   * a rescan from the web UI is explicit, at most one per SCAN_COOLDOWN_MS,
#     and a soft machine.Timer keeps the watchdog fed while it runs (scheduled
#     callbacks still run inside the CYW43 wait loop), so the 10 s worst case
#     can't trip the 8 s watchdog. The C-level Bluetooth stack keeps being
#     polled during the wait; only Python tasks (display, web) pause.

from gitmatrix import _compat, _log

SETUP = "setup"
CONNECTING = "connecting"
FALLBACK = "fallback"
CONNECTED = "connected"
UNAVAILABLE = "unavailable"

AP_IP = "192.168.4.1"
FALLBACK_MS = 60000                      # configured network failing this long: AP up too
RETRY_MS = (15000, 30000, 60000)         # between connect attempts, last one repeats
TICK_MS = 500
SCAN_MAX = 30
SCAN_COOLDOWN_MS = 15000                 # between web-triggered rescans
SCAN_FEED_MS = 1000                      # watchdog keepalive period during a scan
MIN_AP_PASSWORD = 8                      # WPA2 needs 8-63 characters

DEFAULT_AP_SSID = "GitMatrix"
DEFAULT_AP_PASSWORD = "monalisa"
DEFAULT_HOSTNAME = "gitmatrix"

_STATUS = {
    0: "idle",
    1: "connecting",
    2: "no ip yet",
    3: "connected",
    -1: "connection failed",
    -2: "network not found",
    -3: "wrong password",
}


def valid_hostname(name):
    """RFC 1123 label: letters, digits and hyphens, not starting/ending with '-'."""
    if not isinstance(name, str) or not 0 < len(name) <= 32:
        return False
    if name[0] == "-" or name[-1] == "-":
        return False
    return all(c in _HOST_CHARS for c in name.lower())


_HOST_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"


def _start_dns(ip):
    from phew import dns

    dns.run_catchall(ip)


def _keepalive(feed):
    """A soft periodic Timer calling feed(); None if there's no Timer."""
    if feed is None:
        return None
    feed()
    try:
        from machine import Timer

        t = Timer(-1)
        t.init(mode=Timer.PERIODIC, period=SCAN_FEED_MS, callback=lambda _t: feed())
        return t
    except (ImportError, AttributeError, TypeError, ValueError, OSError) as e:
        _log.log(f"wifi: no scan keepalive ({_compat.error_text(e)})")
        return None


class WiFi:
    def __init__(self, config, firmware=None, network_=None, dns=None):
        self.config = config
        self.firmware = firmware or {}
        if network_ is None:
            try:
                import network as network_
            except ImportError:
                network_ = None
        self.net = network_
        self.dns = dns if dns is not None else _start_dns
        self.sta = None
        self.ap = None
        self.mode = UNAVAILABLE
        self.ap_on = False
        self.dns_started = False
        self.since = 0           # when the current connecting spell began
        self.attempt_at = 0
        self.attempts = 0
        self.networks = []       # [{"ssid", "rssi", "secure", "channel"}] from the last scan
        self.scanned_at = None
        self.scanning = False
        self.ip = None

    # ---- settings -----------------------------------------------------------

    @property
    def ssid(self):
        return self.config.get("wifi_ssid") or None

    @property
    def hostname(self):
        name = self.config.get("wifi_hostname") or DEFAULT_HOSTNAME
        return name if valid_hostname(name) else DEFAULT_HOSTNAME

    @property
    def ap_ssid(self):
        return self.firmware.get("AP_SSID") or DEFAULT_AP_SSID

    @property
    def ap_password(self):
        pw = self.firmware.get("AP_PASSWORD") or DEFAULT_AP_PASSWORD
        if not isinstance(pw, str) or not MIN_AP_PASSWORD <= len(pw) <= 63:
            _log.log(f"wifi: AP_PASSWORD must be 8-63 characters, using '{DEFAULT_AP_PASSWORD}'")
            return DEFAULT_AP_PASSWORD
        return pw

    # ---- lifecycle ----------------------------------------------------------

    def start(self, now=None):
        now = _compat.ticks_ms() if now is None else now
        net = self.net
        if net is None:
            self.mode = UNAVAILABLE
            return self
        try:
            # must happen before the interfaces come up for DHCP/mDNS to use it
            net.hostname(self.hostname)
        except (AttributeError, ValueError, OSError) as e:
            _log.log(f"wifi: hostname not set ({_compat.error_text(e)})")
        self.sta = net.WLAN(net.STA_IF)
        self.ap = net.WLAN(net.AP_IF)
        # The CYW43 keeps its interfaces up across a soft reset (a reboot). Take
        # them down so the old network/AP go away and mDNS, which registers the
        # hostname as an interface comes up, answers to the current name.
        for wlan in (self.sta, self.ap):
            try:
                if wlan.active():
                    wlan.active(False)
            except OSError as e:
                _log.log(f"wifi: reset failed {_compat.error_text(e)}")
        if self.ssid is None:
            self.mode = SETUP
            self._ap(True)
            return self
        self._ap(False)
        self.sta.active(True)
        self.mode = CONNECTING
        self.since = now
        self._connect(now)
        return self

    def _connect(self, now):
        self.attempt_at = now
        self.attempts += 1
        pw = self.config.get("wifi_password") or None
        _log.log(f"wifi: joining '{self.ssid}' (attempt {self.attempts})")
        try:
            if self.attempts > 1:
                self.sta.disconnect()
            if pw:
                self.sta.connect(self.ssid, pw)
            else:
                self.sta.connect(self.ssid)
        except OSError as e:
            _log.log(f"wifi: connect failed {_compat.error_text(e)}")

    def _ap(self, on):
        if self.ap is None or on == self.ap_on:
            return
        self.ap_on = on
        try:
            if on:
                self.ap.config(essid=self.ap_ssid, password=self.ap_password)
                # the CYW43 defaults to WPA2-AES once a password is set; be explicit where we can
                sec = getattr(self.ap, "SEC_WPA2", None)
                if sec is not None:
                    self.ap.config(security=sec)
                self.ap.active(True)
                _log.log(f"wifi: access point '{self.ap_ssid}' up at {AP_IP}")
                if not self.dns_started:
                    self.dns_started = True
                    self.dns(AP_IP)
            else:
                self.ap.active(False)
                _log.log("wifi: access point off")
        except (OSError, ValueError, TypeError) as e:
            _log.log(f"wifi: access point {'start' if on else 'stop'} failed {_compat.error_text(e)}")

    def tick(self, now=None):
        now = _compat.ticks_ms() if now is None else now
        if self.mode in (UNAVAILABLE, SETUP):
            return self.mode
        try:
            up = self.sta.isconnected()
        except OSError:
            up = False
        if up:
            if self.mode != CONNECTED:
                self.mode = CONNECTED
                self.attempts = 0
                self.ip = self._sta_ip()
                _log.log(f"wifi: connected to '{self.ssid}' as {self.ip} ({self.hostname}.local)")
                self._ap(False)
            return self.mode
        if self.mode == CONNECTED:
            _log.log("wifi: connection lost")
            self.mode = CONNECTING
            self.since = now
            self.ip = None
            self.attempts = 0
            self._connect(now)
            return self.mode
        if self.mode == CONNECTING and _compat.ticks_diff(now, self.since) >= FALLBACK_MS:
            _log.log(f"wifi: '{self.ssid}' unreachable for {FALLBACK_MS // 1000}s ({self.sta_status()})")
            self.mode = FALLBACK
            self._ap(True)
        delay = RETRY_MS[min(self.attempts, len(RETRY_MS)) - 1]
        if _compat.ticks_diff(now, self.attempt_at) >= delay:
            self._connect(now)
        return self.mode

    # ---- scanning -----------------------------------------------------------

    def scan_wait_ms(self, now=None):
        """How long until another scan is allowed (0 = now)."""
        if self.scanned_at is None:
            return 0
        now = _compat.ticks_ms() if now is None else now
        return max(0, SCAN_COOLDOWN_MS - _compat.ticks_diff(now, self.scanned_at))

    def prescan(self, feed=None):
        """Setup mode at boot, before the event loop runs: fill the network list
        for the provisioning page so nobody has to scan from inside the loop."""
        if self.mode != SETUP:
            return []
        _log.log("wifi: scanning for the setup page")
        return self.scan(feed)

    def scan(self, feed=None):
        """Blocking (~1-3 s, 10 s cap in the CYW43 driver). Pass the watchdog's
        feed() and it is called every SCAN_FEED_MS for the duration."""
        if self.net is None:
            return []
        self.scanning = True
        keepalive = _keepalive(feed)
        try:
            if self.sta is None:
                self.sta = self.net.WLAN(self.net.STA_IF)
            if not self.sta.active():
                # scanning needs the station interface even in AP-only setup mode
                self.sta.active(True)
            raw = self.sta.scan()
        except OSError as e:
            _log.log(f"wifi: scan failed {_compat.error_text(e)}")
            raw = []
        finally:
            if keepalive is not None:
                keepalive.deinit()
            if feed is not None:
                feed()
            self.scanning = False
        best = {}
        for entry in raw:
            try:
                ssid = entry[0].decode("utf-8") if isinstance(entry[0], (bytes, bytearray)) else str(entry[0])
            except UnicodeError:
                continue
            ssid = ssid.strip("\x00")
            if not ssid:
                continue
            rssi = entry[3]
            if ssid not in best or rssi > best[ssid]["rssi"]:
                best[ssid] = {"ssid": ssid, "rssi": rssi, "secure": bool(entry[4]), "channel": entry[2]}
        self.networks = sorted(best.values(), key=lambda n: -n["rssi"])[:SCAN_MAX]
        self.scanned_at = _compat.ticks_ms()
        return self.networks

    # ---- status -------------------------------------------------------------

    def _sta_ip(self):
        try:
            return self.sta.ifconfig()[0]
        except (OSError, AttributeError, IndexError):
            return None

    def sta_status(self):
        try:
            return _STATUS.get(self.sta.status(), str(self.sta.status()))
        except (OSError, AttributeError, TypeError):
            return "unknown"

    def connected(self):
        return self.mode == CONNECTED

    def url(self):
        """Where to point a browser, or None."""
        if self.mode == CONNECTED and self.ip:
            return f"http://{self.ip}"
        if self.ap_on:
            return f"http://{AP_IP}"
        return None

    def status(self):
        return {
            "mode": self.mode,
            "connected": self.mode == CONNECTED,
            "ssid": self.ssid,
            "ip": self.ip if self.mode == CONNECTED else None,
            "hostname": self.hostname,
            "url": self.url(),
            "mdns": f"http://{self.hostname}.local" if self.mode == CONNECTED else None,
            "sta_status": self.sta_status() if self.sta is not None and self.mode != SETUP else None,
            "ap": {"on": self.ap_on, "ssid": self.ap_ssid, "ip": AP_IP} if self.ap_on else {"on": False},
            "scanning": self.scanning,
        }

    def status_text(self):
        """One line for the menu's WiFi screen (scrolls on the 53x7 display)."""
        if self.mode == UNAVAILABLE:
            return "WiFi unavailable"
        if self.mode == CONNECTED:
            return f"{self.ssid} - visit http://{self.ip} or http://{self.hostname}.local"
        ap = f"join WiFi '{self.ap_ssid}' password '{self.ap_password}' then visit http://{AP_IP}"
        if self.mode == SETUP:
            return f"Setup: {ap}"
        if self.mode == FALLBACK:
            return f"Can't reach '{self.ssid}' ({self.sta_status()}) - {ap}"
        return f"Connecting to '{self.ssid}'..."
