# Background data fetching for the widget apps.
#
# Every source is a generator that yields between network reads, so `Data.poll()` can be
# called once per frame and animations keep running while data downloads. Only the
# TLS handshake at the start of each request blocks (~0.5-1s).

import gc
import json
import time

import config

CACHE_FILE = "/cache.json"
CONTRIBS_FILE = "/contrib_data.json"

MIN = 60 * 1000
HOUR = 60 * MIN
RETRY_MS = 15 * MIN
WIFI_TIMEOUT_MS = 30 * 1000

UA = {"User-Agent": "contribution-widget"}
# Mobile page is ~1MB rather than ~2.7MB; SOCS skips the EU cookie consent redirect
YT_HEADERS = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "Cookie": "SOCS=CAI"}

try:
    import secrets
except ImportError:
    secrets = None


def secret(name, default=None):
    return getattr(secrets, name, default)


def http_chunks(url, headers, size=2048):
    import requests
    gc.collect()
    r = requests.get(url, headers=headers)
    try:
        if r.status_code != 200:
            raise ValueError("HTTP %d" % r.status_code)
        while True:
            chunk = r.raw.read(size)
            if not chunk:
                break
            yield chunk
    finally:
        r.close()


def fetch_json(url, headers=UA):
    parts = []
    for chunk in http_chunks(url, headers):
        parts.append(chunk)
        yield
    return json.loads(b"".join(parts))


def github_headers():
    token = secret("GITHUB_TOKEN")
    if token:
        h = dict(UA)
        h["Authorization"] = "Bearer " + token
        return h
    return UA


def parse_abbrev(s):
    # "711k" -> 711000, "1.2M" -> 1200000, "12,345" -> 12345
    s = s.strip().replace(",", "")
    mult = 1
    if s[-1:] in "kK":
        mult, s = 1000, s[:-1]
    elif s[-1:] in "mM":
        mult, s = 1000000, s[:-1]
    elif s[-1:] in "bB":
        mult, s = 1000000000, s[:-1]
    return int(float(s) * mult)


# --- Sources: each returns (value, approximate) ---------------------------------------

def src_ntp():
    import ntptime
    yield
    ntptime.settime()
    return time.time(), False


def src_contribs():
    user = secret("GITHUB_USERNAME")
    parts = []
    for chunk in http_chunks("https://github.com/%s.contribs" % user, UA):
        parts.append(chunk)
        yield
    body = b"".join(parts)
    data = json.loads(body)
    if "weeks" not in data:
        raise ValueError("bad contribs")
    with open(CONTRIBS_FILE, "wb") as f:
        f.write(body)
    return data.get("total_contributions", 0), False


def src_stars():
    d = yield from fetch_json("https://api.github.com/repos/" + config.STARS_REPO, github_headers())
    return d["stargazers_count"], False


def src_followers():
    d = yield from fetch_json("https://api.github.com/users/" + secret("GITHUB_USERNAME"), github_headers())
    return d["followers"], False


def src_x():
    # No free official API; FxTwitter's public API exposes follower counts
    d = yield from fetch_json("https://api.fxtwitter.com/" + config.X_HANDLE)
    if d.get("code") != 200:
        raise ValueError(d.get("message", "X lookup failed"))
    return d["user"]["followers"], False


def src_youtube():
    key = secret("YOUTUBE_API_KEY")
    if key:
        d = yield from fetch_json(
            "https://www.googleapis.com/youtube/v3/channels?part=statistics&forHandle=%s&key=%s"
            % (config.YOUTUBE_HANDLE, key))
        return int(d["items"][0]["statistics"]["subscriberCount"]), False
    # Without an API key, scan the public channel page for "711k subscribers"
    tail = b""
    for chunk in http_chunks("https://m.youtube.com/" + config.YOUTUBE_HANDLE, YT_HEADERS, 4096):
        buf = tail + chunk
        i = buf.find(b" subscribers")
        if i >= 0:
            token = buf[max(0, i - 24):i].decode()
            for sep in ("\\x22", '"', ">", " "):
                token = token.split(sep)[-1]
            return parse_abbrev(token), True
        tail = buf[-40:]
        yield
    raise ValueError("subscriber count not found")


# name: (generator, refresh interval, needs)
SOURCES = {
    "ntp": (src_ntp, 6 * HOUR),
    "contribs": (src_contribs, 24 * HOUR),
    "stars": (src_stars, HOUR),
    "followers": (src_followers, HOUR),
    "x": (src_x, HOUR),
    "youtube": (src_youtube, 6 * HOUR),
}

APP_SOURCES = {"graph": "contribs", "clock": "ntp", "stars": "stars",
               "followers": "followers", "x": "x", "youtube": "youtube"}


class Data:
    def __init__(self, apps):
        try:
            with open(CACHE_FILE) as f:
                self.values = json.load(f)
        except (OSError, ValueError):
            self.values = {}
        now = time.ticks_ms()
        # Clock first so the time is right quickly; slow YouTube scrape last
        order = ("ntp", "contribs", "stars", "followers", "x", "youtube")
        wanted = set(APP_SOURCES[a] for a in apps if a in APP_SOURCES)
        self.names = [n for n in order if n in wanted]
        self.due = {n: now for n in self.names}
        self.job = None
        self.job_name = None
        self.wifi_since = None
        self.version = 0  # bumped whenever any value changes
        self.time_synced = False

    def get(self, name):
        # Returns (value, approximate) or None
        v = self.values.get(name)
        return (v[0], v[1]) if v else None

    def refresh_all(self):
        now = time.ticks_ms()
        for n in self.names:
            self.due[n] = now

    def busy(self):
        return self.job is not None or any(time.ticks_diff(time.ticks_ms(), d) >= 0 for d in self.due.values())

    def _wifi(self, now):
        import network
        wlan = network.WLAN(network.STA_IF)
        if wlan.isconnected():
            self.wifi_since = None
            return True
        ssid = secret("WIFI_SSID")
        if not ssid:
            return False
        if self.wifi_since is None:
            wlan.active(True)
            wlan.connect(ssid, secret("WIFI_PASSWORD"))
            self.wifi_since = now
        elif time.ticks_diff(now, self.wifi_since) > WIFI_TIMEOUT_MS:
            print("data: WiFi timed out")
            self.wifi_since = None
            for n in self.names:
                if time.ticks_diff(now, self.due[n]) >= 0:
                    self.due[n] = time.ticks_add(now, RETRY_MS)
        return False

    def poll(self):
        now = time.ticks_ms()
        if self.job is None:
            name = None
            for n in self.names:
                if time.ticks_diff(now, self.due[n]) >= 0:
                    name = n
                    break
            if name is None or not self._wifi(now):
                return
            self.job_name = name
            self.job = SOURCES[name][0]()
        name = self.job_name
        try:
            next(self.job)
            return
        except StopIteration as done:
            value = done.value
            self.values[name] = [value[0], value[1]]
            if name == "ntp":
                self.time_synced = True
            else:
                self._save()
            self.version += 1
            self.due[name] = time.ticks_add(now, SOURCES[name][1])
            print("data:", name, "=", value[0])
        except Exception as e:  # noqa: BLE001
            print("data:", name, "failed:", e)
            self.due[name] = time.ticks_add(now, RETRY_MS)
        self.job = None
        self.job_name = None

    def _save(self):
        values = {k: v for k, v in self.values.items() if k != "ntp"}
        with open(CACHE_FILE, "w") as f:
            json.dump(values, f)
