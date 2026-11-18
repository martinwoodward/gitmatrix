# Notifications: POST /api/v1/notify puts one on a small queue; the notifier
# task fetches any image/sound URL, decodes the PNG, then hands it to the host,
# which interrupts the current plugin, shows it, plays its sound alongside and
# puts everything back afterwards.
#
#   parse()     check a request body against the limits -> Note (or NotifyError)
#   Queue       8 slots, priority with aging, ttl expiry, id replaces
#   Renderer    draws text / image / icon+text for a Note, frame by frame
#   Notifier    the queue, the asset prefetch task and the sound policy

import binascii

from gitmatrix import _compat, _display, _events, _log, _png, _runtime, _sounds

QUEUE_SIZE = 8
AGE_MS = 60000          # a normal note waiting this long ranks with a fresh high one
TTL_S = 600             # default time a note may wait in the queue
MAX_TTL_S = 86400
MAX_DURATION_S = 60
MAX_REPEAT = 5
PLUGIN_MS = 10000       # default time a forced plugin is shown
STATIC_MS = 3000        # time a still image or short text shows per pass
SPEED = 25              # default scroll speed, pixels per second
MIN_SPEED = 5
MAX_SPEED = 100
MAX_TEXT = 500
MAX_URL = 512
GRACE_MS = 2000         # after a dismiss, a second press opens the menu
FETCH_TIMEOUT_MS = 15000

WIDTH = 53
HEIGHT = 7

FIELDS = ("text", "image", "image_url", "sound", "sound_url", "sound_data", "plugin", "data",
          "duration", "repeat", "priority", "ttl", "color", "font", "speed", "id")


class NotifyError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Note:
    def __init__(self):
        self.id = None
        self.text = None
        self.png = None          # PNG bytes (inline or fetched)
        self.image_url = None
        self.image = None        # decoded picovector image, set by prepare()
        self.sound = None        # built-in or custom sound name
        self.sound_url = None
        self.sound_data = None   # WAV bytes (inline or fetched)
        self.plugin = None
        self.data = None
        self.duration = None     # ms, or None for automatic
        self.repeat = 1
        self.high = False
        self.ttl = TTL_S
        self.color = (255, 255, 255)
        self.font = None
        self.speed = SPEED
        self.created = 0
        self.expires = 0
        self.cancelled = False   # dismissed via the API while showing
        self.replaced = False    # a newer note with the same id is on its way
        self.sound_result = None
        self.ended = False       # the end event has gone out

    def summary(self):
        return {"id": self.id, "text": self.text, "plugin": self.plugin,
                "priority": "high" if self.high else "normal",
                "image": bool(self.png or self.image_url), "sound": self.sound or bool(self.sound_url or self.sound_data)}


# ---- validation -------------------------------------------------------------

def _b64(value, limit, what):
    if not isinstance(value, str):
        raise NotifyError(f"{what} must be a base64 string")
    if (len(value) // 4) * 3 > limit + 2:
        raise NotifyError(f"{what} larger than {limit // 1024} KB", 413)
    try:
        data = binascii.a2b_base64(value)
    except (ValueError, TypeError) as e:
        raise NotifyError(f"{what} is not valid base64") from e
    if len(data) > limit:
        raise NotifyError(f"{what} larger than {limit // 1024} KB", 413)
    return data


def _url(value, what):
    if not isinstance(value, str) or len(value) > MAX_URL or not (value.startswith("http://") or value.startswith("https://")):  # noqa: PIE810 - MicroPython has no tuple startswith
        raise NotifyError(f"{what} must be an http(s) URL")
    return value


def _number(body, key, lo, hi, default, integer=False):
    if key not in body or body[key] is None:
        return default
    v = body[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (integer and not isinstance(v, int)):
        raise NotifyError(f"{key} must be a number")
    if v < lo or v > hi:
        raise NotifyError(f"{key} must be {lo}-{hi}")
    return v


def parse_color(value):
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        raise NotifyError("color must be #rrggbb")
    try:
        n = int(value[1:], 16)
    except ValueError as e:
        raise NotifyError("color must be #rrggbb") from e
    return (n >> 16) & 255, (n >> 8) & 255, n & 255


_next_id = 0


def _new_id():
    global _next_id
    _next_id += 1
    return f"n{_next_id}"


def parse(body, now=None, loader=None, config=None):
    """Validate a notify request body. Raises NotifyError(message, status)."""
    if not isinstance(body, dict):
        raise NotifyError("body must be a JSON object")
    for key in body:
        if key not in FIELDS:
            raise NotifyError(f"unknown field: {key}")
    now = _compat.ticks_ms() if now is None else now
    n = Note()

    text = body.get("text")
    if text is not None:
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            raise NotifyError(f"text must be a string of up to {MAX_TEXT} characters")
        n.text = text or None

    if body.get("image") is not None and body.get("image_url") is not None:
        raise NotifyError("give image or image_url, not both")
    if body.get("image") is not None:
        n.png = _b64(body["image"], _png.MAX_BYTES, "image")
        try:
            _png.check(n.png)
        except _png.PNGError as e:
            raise NotifyError(str(e), e.status) from e
    if body.get("image_url") is not None:
        n.image_url = _url(body["image_url"], "image_url")

    sounds = [k for k in ("sound", "sound_url", "sound_data") if body.get(k) is not None]
    if len(sounds) > 1:
        raise NotifyError("give one of sound, sound_url or sound_data")
    if body.get("sound") is not None:
        name = body["sound"]
        if not isinstance(name, str) or _sounds.find(name) is None:
            raise NotifyError(f"unknown sound: {name}")
        n.sound = name
    if body.get("sound_url") is not None:
        n.sound_url = _url(body["sound_url"], "sound_url")
    if body.get("sound_data") is not None:
        n.sound_data = _b64(body["sound_data"], _sounds.MAX_INLINE, "sound_data")
        try:
            _sounds.parse_wav(n.sound_data)
        except ValueError as e:
            raise NotifyError(f"sound_data: {e}") from e

    plugin = body.get("plugin")
    if plugin is not None:
        if n.text or n.png or n.image_url:
            raise NotifyError("plugin can't be combined with text or image")
        if not isinstance(plugin, str) or not _compat.valid_name(plugin):
            raise NotifyError("bad plugin name")
        installed = loader.on_disk() if loader is not None else []
        if plugin not in installed:
            raise NotifyError(f"plugin not installed: {plugin}")
        if config is not None and plugin in config.get("quarantined", {}):
            raise NotifyError(f"plugin is quarantined: {plugin}", 409)
        n.plugin = plugin
        n.data = body.get("data")
    elif body.get("data") is not None:
        raise NotifyError("data needs a plugin")

    if not (n.text or n.png or n.image_url or n.plugin):
        raise NotifyError("nothing to show: give text, image, image_url or plugin")

    duration = _number(body, "duration", 0, MAX_DURATION_S, None)
    if duration is not None:
        if duration <= 0:
            raise NotifyError(f"duration must be 0-{MAX_DURATION_S}")
        n.duration = int(duration * 1000)
    n.repeat = _number(body, "repeat", 1, MAX_REPEAT, 1, integer=True)
    priority = body.get("priority", "normal")
    if priority not in ("normal", "high"):
        raise NotifyError("priority must be normal or high")
    n.high = priority == "high"
    n.ttl = _number(body, "ttl", 1, MAX_TTL_S, TTL_S)
    if body.get("color") is not None:
        n.color = parse_color(body["color"])
    f = body.get("font")
    if f is not None:
        if not isinstance(f, str) or not _compat.valid_name(f):
            raise NotifyError("bad font name")
        n.font = f
    n.speed = _number(body, "speed", MIN_SPEED, MAX_SPEED, SPEED)
    nid = body.get("id")
    if nid is not None:
        if not isinstance(nid, str) or not nid or len(nid) > 64:
            raise NotifyError("id must be a string of up to 64 characters")
        n.id = nid
    else:
        n.id = _new_id()
    n.created = now
    n.expires = _compat.ticks_add(now, int(n.ttl * 1000))
    return n


# ---- queue ------------------------------------------------------------------

class Queue:
    def __init__(self, size=QUEUE_SIZE):
        self.size = size
        self.items = []

    def __len__(self):
        return len(self.items)

    def expire(self, now):
        keep = [n for n in self.items if _compat.ticks_diff(now, n.expires) < 0]
        dropped = len(self.items) - len(keep)
        if dropped:
            self.items = keep
            _log.log(f"notify: {dropped} expired")
        return dropped

    def push(self, note, now):
        for i, old in enumerate(self.items):
            if old.id == note.id:
                self.items[i] = note
                return
        self.expire(now)
        if len(self.items) >= self.size:
            raise NotifyError("notification queue is full", 429)
        self.items.append(note)

    @staticmethod
    def score(note, now):
        return (AGE_MS if note.high else 0) + _compat.ticks_diff(now, note.created)

    def peek(self, now):
        self.expire(now)
        best = None
        for n in self.items:
            if best is None or self.score(n, now) > self.score(best, now):
                best = n
        return best

    def pop(self, now):
        best = self.peek(now)
        if best is not None:
            self.items.remove(best)
        return best

    def remove(self, nid):
        before = len(self.items)
        self.items = [n for n in self.items if n.id != nid]
        return before - len(self.items)

    def clear(self):
        n = len(self.items)
        self.items = []
        return n

    def has_high(self):
        for n in self.items:
            if n.high:
                return True
        return False


# ---- alert sound policy -----------------------------------------------------

def _minutes(value):
    if isinstance(value, int):
        return (value % 24) * 60
    try:
        h, m = str(value).split(":")
        return (int(h) % 24) * 60 + int(m) % 60
    except ValueError:
        return 0


def quiet_window(config):
    """(start minutes, end minutes, days) - accepts the old (22, 7) form too."""
    hours = config.get("quiet_hours", ("22:00", "07:00"))
    try:
        start, end = hours
    except (TypeError, ValueError):
        start, end = "22:00", "07:00"
    days = config.get("quiet_days", [0, 1, 2, 3, 4, 5, 6])
    return _minutes(start), _minutes(end), days


def in_quiet_hours(config, t=None):
    """t is a localtime tuple (weekday at [6], 0 = Monday). A window that
    crosses midnight belongs to the day it starts on."""
    if not config.get("quiet_hours_enabled", False):
        return False
    if t is None:
        t = _localtime()
        if t is None:
            return False
    start, end, days = quiet_window(config)
    m = t[3] * 60 + t[4]
    wd = t[6]
    if start == end:
        return wd in days
    if start < end:
        return start <= m < end and wd in days
    if m >= start:
        return wd in days
    return m < end and (wd - 1) % 7 in days


def _localtime():
    try:
        import timezones
        t = timezones.localtime()
    except Exception:  # noqa: BLE001 - no clock means no quiet hours
        return None
    return t if t[0] >= 2024 else None   # clock not set yet


def sound_policy(config, high, streaming=False, t=None, force=False):
    """What to do with a note's sound: play, interrupt (play over the stream),
    or one of the silent reasons: off, quiet, streaming. `force` (POST /sound)
    overrides the alert switch and quiet hours, never a Bluetooth stream."""
    if not force:
        if not config.get("alert_sound", True):
            return "off"
        if in_quiet_hours(config, t) and not (high and config.get("quiet_bypass_high", False)):
            return "quiet"
    if streaming:
        if high and config.get("alert_interrupt_audio", False):
            return "interrupt"
        return "streaming"
    return "play"


# ---- rendering --------------------------------------------------------------

IMG_STATIC = 0
IMG_HSCROLL = 1
IMG_VSCROLL = 2
TEXT = 3

_BLACK = None


def _font(name):
    f = None
    if name:
        f = getattr(font, name, None)
    return f if f is not None else font.sins


class Renderer:
    """Precomputes a note's timeline so each frame is a little integer maths
    and a blit/text call.

      image only      still if it fits, else scrolls (wide: sideways, tall: up)
      text only       still if it fits, else scrolls right to left
      icon + text     image <= 7x7: fixed at the left, text in the rest
      image + text    the image's pass, then the text's
    """

    def __init__(self, note, target):
        global _BLACK
        if _BLACK is None:
            _BLACK = color.rgb(0, 0, 0)
        self.note = note
        self.img = note.image
        self.text = note.text
        self.pen = color.rgb(*note.color)
        self.font = _font(note.font)
        self.speed = note.speed
        self.segments = []          # (kind, ms)
        self.icon = False
        self.x0 = 0                 # text area starts here (after an icon)
        self.tw = self.th = 0
        img = self.img
        if img is not None and self.text and img.width <= HEIGHT and img.height <= HEIGHT:
            self.icon = True
            self.x0 = img.width + 1
        elif img is not None:
            w, h = img.width, img.height
            if w > WIDTH:
                self.segments.append((IMG_HSCROLL, self._scroll_ms(WIDTH + w)))
            elif h > HEIGHT:
                self.segments.append((IMG_VSCROLL, self._scroll_ms(HEIGHT + h)))
            else:
                self.segments.append((IMG_STATIC, STATIC_MS))
        if self.text:
            target.font = self.font
            self.tw, self.th = target.measure_text(self.text)
            self.tw = int(self.tw)
            self.th = int(self.th)
            area = WIDTH - self.x0
            self.scroll = self.tw > area
            self.segments.append((TEXT, self._scroll_ms(area + self.tw) if self.scroll else STATIC_MS))
        self.pass_ms = 0
        for _, ms in self.segments:
            self.pass_ms += ms
        self.pass_ms = max(1, self.pass_ms)
        if note.duration:
            self.total_ms = note.duration
        else:
            self.total_ms = min(MAX_DURATION_S * 1000, self.pass_ms * note.repeat)

    def _scroll_ms(self, pixels):
        return pixels * _display.ms_per_px(self.speed)

    def draw(self, target, elapsed):
        """Draw the frame at `elapsed` ms. Returns True once the note is done."""
        if elapsed >= self.total_ms:
            return True
        t = elapsed % self.pass_ms
        target.pen = _BLACK
        target.clear()
        kind = TEXT
        for seg in self.segments:
            if t < seg[1]:
                kind = seg[0]
                break
            t -= seg[1]
        moved = _display.scroll_px(t, self.speed)
        img = self.img
        if kind == IMG_STATIC:
            target.blit(img, vec2((WIDTH - img.width) // 2, (HEIGHT - img.height) // 2))
        elif kind == IMG_HSCROLL:
            target.blit(img, vec2(WIDTH - moved, (HEIGHT - img.height) // 2))
        elif kind == IMG_VSCROLL:
            target.blit(img, vec2((WIDTH - img.width) // 2, HEIGHT - moved))
        else:
            area = WIDTH - self.x0
            x = WIDTH - moved if self.scroll else self.x0 + (area - self.tw) // 2
            target.font = self.font
            target.pen = self.pen
            target.text(self.text, vec2(x, (HEIGHT - self.th) // 2))
            if self.icon:
                target.pen = _BLACK
                target.rectangle(0, 0, self.x0, HEIGHT)
                target.blit(img, vec2(0, (HEIGHT - img.height) // 2))
        return False


# ---- the notifier -----------------------------------------------------------

class Notifier:
    def __init__(self, config, loader=None):
        self.config = config
        self.loader = loader
        self.queue = Queue()
        self.pending = None         # prepared, waiting for the host to take it
        self.active = None          # on screen
        self.preparing = None
        self.grace_until = None
        self.sound_task = None
        self.shown = 0
        self.dropped = 0
        self.last = None            # summary of the last note shown
        self.last_error = None
        _runtime.notifier = self

    # -- API side

    def submit(self, body, now=None):
        now = _compat.ticks_ms() if now is None else now
        note = parse(body, now, self.loader, self.config)
        if self.active is not None and self.active.id == note.id:
            self.active.replaced = True
            note.high = True        # jump the queue: it takes over the screen
        if self.pending is not None and self.pending.id == note.id:
            self.pending = None
        if self.preparing is not None and self.preparing.id == note.id:
            self.preparing.cancelled = True
        self.queue.push(note, now)
        return note

    def dismiss(self, nid=None):
        """Dismiss the note showing (and with no id, clear the queue too)."""
        count = 0
        if nid is None:
            count += self.queue.clear()
            if self.pending is not None:
                self.pending = None
                count += 1
            if self.preparing is not None:
                self.preparing.cancelled = True
        else:
            count += self.queue.remove(nid)
            if self.pending is not None and self.pending.id == nid:
                self.pending = None
                count += 1
            if self.preparing is not None and self.preparing.id == nid:
                self.preparing.cancelled = True
                count += 1
        if self.active is not None and (nid is None or self.active.id == nid):
            self.active.cancelled = True
            self.active.replaced = False
            count += 1
        return count

    def status(self):
        return {
            "active": self.active.summary() if self.active is not None else None,
            "queued": [n.summary() for n in ([self.pending] if self.pending else []) + self.queue.items],
            "shown": self.shown,
            "dropped": self.dropped,
            "last": self.last,
            "last_error": self.last_error,
        }

    def waiting_high(self):
        return (self.pending is not None and self.pending.high) or self.queue.has_high()

    # -- host side

    def take(self, now):
        """The next prepared note, if one is ready and we're not in the grace
        period after a dismiss. Starts its sound."""
        if self.pending is None:
            return None
        if self.grace_until is not None:
            if _compat.ticks_diff(now, self.grace_until) < 0:
                return None
            self.grace_until = None
        note, self.pending = self.pending, None
        self.active = note
        self.shown += 1
        self.last = note.summary()
        self.start_sound(note)
        _emit({"action": "start", "id": note.id, "plugin": note.plugin, "text": note.text,
               "priority": "high" if note.high else "normal"}, note.id)
        return note

    def finished(self, note, dismissed, now):
        if self.active is note:
            self.active = None
        if not note.ended:
            note.ended = True
            _emit({"action": "end", "id": note.id, "dismissed": bool(dismissed)}, None)
        if dismissed:
            self.stop_sound()
            self.grace_until = _compat.ticks_add(now, GRACE_MS)

    def start_sound(self, note):
        source = None
        if note.sound_data is not None:
            source = note.sound_data
        elif note.sound is not None:
            source = _sounds.find(note.sound)
        if source is None:
            return
        self.stop_sound()
        coro = _sounds.alert(source, note.high, self.config)
        try:
            import asyncio
            self.sound_task = asyncio.create_task(coro)
        except RuntimeError:        # no running loop (host tests drive frames directly)
            coro.close()
            self.sound_task = None

    def stop_sound(self):
        task = self.sound_task
        self.sound_task = None
        if task is not None:
            try:
                if not task.done():
                    task.cancel()
            except AttributeError:
                task.cancel()

    # -- the task

    async def prepare(self, note):
        """Fetch URL assets and decode the image; runs before the note shows."""
        if note.image_url is not None:
            note.png = await _fetch(note.image_url, _png.MAX_BYTES)
        if note.sound_url is not None:
            data = await _fetch(note.sound_url, _sounds.MAX_URL_BYTES)
            _sounds.parse_wav(data)
            note.sound_data = data
        if note.png is not None:
            _compat.collect()
            note.image = _png.decode(note.png)
            note.png = None if note.image_url is None else note.png

    async def step(self, now=None):
        """Prepare the next note if there's room for one. Returns True if it did work."""
        now = _compat.ticks_ms() if now is None else now
        if self.pending is not None:
            return False
        note = self.queue.pop(now)
        if note is None:
            return False
        self.preparing = note
        try:
            await self.prepare(note)
        except Exception as e:  # noqa: BLE001 - a bad asset drops the note, nothing else
            self.dropped += 1
            self.last_error = f"{note.id}: {_compat.error_text(e)}"
            _log.log(f"notify: dropped {self.last_error}")
            return True
        finally:
            self.preparing = None
        if note.cancelled:
            return True
        if self.pending is not None and self.pending.id == note.id:
            return True
        self.pending = note
        return True

    async def run(self):
        while True:
            try:
                worked = await self.step()
            except Exception as e:  # noqa: BLE001
                _log.log(f"notify: {_compat.error_text(e)}")
                worked = False
            await _sleep_ms(10 if worked else 50)


def _emit(data, active):
    bus = _runtime.events
    if bus is not None:
        bus.state[_events.NOTIFICATION] = active
        bus.emit(_events.NOTIFICATION, data)


def split_url(url):
    """(use_tls, host, port, path) for an http(s) URL."""
    tls = url.startswith("https://")
    rest = url[8:] if tls else url[7:]
    i = rest.find("/")
    hostport, path = (rest, "/") if i < 0 else (rest[:i], rest[i:])
    port = 443 if tls else 80
    if ":" in hostport:
        hostport, p = hostport.rsplit(":", 1)
        port = int(p)
    if not hostport:
        raise ValueError("no host in URL")
    return tls, hostport, port, path


async def _fetch(url, max_bytes):
    """GET a URL into RAM without blocking the loop (TLS is CERT_NONE, as Art Matrix)."""
    import fetch

    tls, host, port, path = split_url(url)
    f = fetch.AsyncFetch(host, port, use_tls=tls, max_bytes=max_bytes, timeout=FETCH_TIMEOUT_MS // 1000)
    deadline = _compat.ticks_add(_compat.ticks_ms(), FETCH_TIMEOUT_MS)
    try:
        f.fetch(path)
        while f.update() == fetch.AsyncFetch.FETCHING:
            if _compat.ticks_diff(_compat.ticks_ms(), deadline) > 0:
                raise OSError("timed out")
            await _sleep_ms(5)
        if f.status != fetch.AsyncFetch.DONE:
            raise OSError(f"HTTP {f.http_status}")
        return bytes(f.body)
    finally:
        f.reset()


async def _sleep_ms(ms):
    import asyncio
    if hasattr(asyncio, "sleep_ms"):
        await asyncio.sleep_ms(ms)
    else:
        await asyncio.sleep(ms / 1000)
