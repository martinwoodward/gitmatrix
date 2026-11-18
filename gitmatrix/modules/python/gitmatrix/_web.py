# The web UI and JSON API, served by Art Matrix's phew. Pages are static files in
# /html that call the JSON endpoints under /api/v1/ (phases 5 and 6 extend these
# for the full REST API and MQTT/Home Assistant).
#
# Security matches Art Matrix: open on the LAN by default. Setting an API token
# (Settings page) makes /api/v1/* need `Authorization: Bearer <token>`; the pages
# keep working because serving a page sets the token as an HttpOnly cookie, and
# cookie-authenticated writes must come from our own origin. That stops other
# web sites and casual scripts, not someone on the LAN with a browser.

import json

from gitmatrix import _audio, _compat, _log, _runtime, _wifi

API = "/api/v1"
COOKIE = "gm_token"
REBOOT_DELAY_MS = 1500
SCAN_WARNING = "audio may stutter while scanning"
MAX_TOKEN = 64

_rt = None          # the boot Runtime
_scan_task = None
_ROUTES = []        # (path, handler, methods), registered with phew by setup()


# ---- plumbing ---------------------------------------------------------------

def _server():
    from phew import server

    return server


def _response(body, status=200, content_type="application/json", headers=None):
    from phew.server import Response

    data = body.encode("utf-8") if isinstance(body, str) else body
    h = {"Content-Type": content_type, "Content-Length": len(data), "Cache-Control": "no-store"}
    if headers:
        h.update(headers)
    return Response(data, status, h)


def reply(obj, status=200):
    return _response(json.dumps(obj), status)


def error(status, message):
    return reply({"error": message}, status)


def spawn(coro):
    """Run a coroutine as a task (tests replace this)."""
    import asyncio

    return asyncio.create_task(coro)


def _token():
    t = _runtime.config.get("api_token") if _runtime.config is not None else None
    return t or None


def _same(a, b):
    """Constant-time string compare."""
    if not isinstance(a, str) or not isinstance(b, str) or len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


def _cookie(request, name):
    for part in request.headers.get("cookie", "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def _host_of(url):
    # "http://gitmatrix.local:80/x" -> "gitmatrix.local:80"
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0]


def authorized(request):
    token = _token()
    if token is None:
        return True
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and _same(auth[7:].strip(), token):
        return True
    if not _same(_cookie(request, COOKIE), token):
        return False
    if request.method in ("GET", "HEAD"):
        return True
    # the cookie rides along on any request, so writes must come from our own pages
    origin = request.headers.get("origin") or request.headers.get("referer")
    return origin is None or _host_of(origin) == request.headers.get("host", "")


def api(path, methods=("GET",)):
    """Register an /api/v1 handler: auth, JSON replies, errors as JSON."""
    def wrap(fn):
        def handler(request, **params):
            if not authorized(request):
                return error(401, "unauthorized")
            try:
                out = fn(request, **params)
            except (ValueError, TypeError, KeyError) as e:
                return error(400, _compat.error_text(e))
            if isinstance(out, tuple):
                return reply(out[0], out[1])
            return out if not isinstance(out, (dict, list)) else reply(out)
        _ROUTES.append((API + path, handler, list(methods)))
        return fn
    return wrap


def _body(request):
    data = request.data
    if not data and request.form:
        data = request.form
    return data if isinstance(data, dict) else {}


def _bool(value):
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes")
    return bool(value)


def _percent(value):
    v = int(float(value) + 0.5)
    if not 0 <= v <= 100:
        raise ValueError("value must be 0-100")
    return v


def schedule_reboot(delay_ms=REBOOT_DELAY_MS):
    """Reboot from housekeeping once this response has gone out."""
    _runtime.reboot_at = _compat.ticks_add(_compat.ticks_ms(), delay_ms)
    _log.log("system: reboot requested")


def _host():
    return _runtime.host


def _config():
    return _runtime.config


def _wifi_mgr():
    return _runtime.wifi


# ---- status -----------------------------------------------------------------

def _plugin_info(name):
    cfg = _config()
    host = _host()
    loader = _runtime.loader
    q = cfg.get("quarantined", {})
    plugin = loader.plugins.get(name) if loader is not None else None
    if plugin is not None:
        title = plugin.title
    else:
        from gitmatrix import _loader

        manifest = _loader.read_manifest(name)
        title = manifest.get("name") if isinstance(manifest.get("name"), str) else name
    return {
        "name": name,
        "title": title,
        "enabled": name not in cfg.get("disabled_plugins", []) and name not in q,
        "quarantined": name in q,
        "reason": q.get(name),
        "current": host is not None and host.current is not None and host.current.name == name,
        "loaded": plugin is not None and plugin.module is not None,
        "errors": plugin.error_count if plugin is not None else 0,
        "last_error": plugin.last_error if plugin is not None else None,
        "slow": bool(plugin.slow) if plugin is not None else False,
    }


def _now_showing():
    host = _host()
    if host is None or host.current is None:
        return None
    return {"name": host.current.name, "title": host.current.title}


def _bt_status():
    audio = _runtime.audio
    return {
        "name": _audio.NAME,
        "connected": bool(audio is not None and audio.connected()),
        "streaming": bool(audio is not None and audio.streaming()),
        "pairable": bool(_config().get("bt_pairable", True)),
        "pairable_supported": _audio.pairable_supported(),
    }


def _input_status():
    host = _host()
    src = host.input if host is not None else None
    if src is None:
        return None
    last = None
    events = src.since(src.seq - 1) if src.seq else []
    if events:
        e = events[-1]
        last = {"kind": e.kind, "value": e.value, "age_ms": _compat.ticks_diff(_compat.ticks_ms(), e.t)}
    return {"seq": src.seq, "last": last}


@api("/status")
def status(_request):
    host = _host()
    cfg = _config()
    wifi = _wifi_mgr()
    import gitmatrix

    return {
        "version": gitmatrix.VERSION,
        "uptime_ms": _compat.ticks_diff(_compat.ticks_ms(), _rt.booted) if _rt is not None else None,
        "now_showing": _now_showing(),
        "menu_open": host is not None and host.mode != 0,
        "safe_mode": host.safe_mode if host is not None else None,
        "plugins": len(host.plugins()) if host is not None else 0,
        "delay": cfg.get("delay", 30),
        "brightness": cfg.get("brightness", 60),
        "volume": _volume_percent(),
        "wifi": wifi.status() if wifi is not None else None,
        "bluetooth": _bt_status(),
        "input": _input_status(),
        "token_required": _token() is not None,
    }


# ---- brightness / volume ----------------------------------------------------

def _volume_percent():
    return int(_config().get("volume", 64) * 100 / 127 + 0.5)


@api("/brightness", ("GET", "POST"))
def brightness(request):
    if request.method == "POST":
        _config().set("brightness", _percent(_body(request)["value"]))
        _host().apply_brightness()
    return {"value": _config().get("brightness", 60)}


@api("/volume", ("GET", "POST"))
def volume(request):
    if request.method == "POST":
        _config().set("volume", int(_percent(_body(request)["value"]) * 127 / 100 + 0.5))
        _host().apply_volume()
    return {"value": _volume_percent()}


# ---- plugins ----------------------------------------------------------------

@api("/plugins")
def plugins(_request):
    cfg = _config()
    return {
        "plugins": [_plugin_info(n) for n in cfg.get("plugins", [])],
        "now_showing": _now_showing(),
        "delay": cfg.get("delay", 30),
    }


def _known(name):
    if name not in _config().get("plugins", []):
        return None, error(404, f"no plugin '{name}'")
    return name, None


@api("/plugins/next", ("POST",))
def plugins_next(_request):
    _host().step(1, _compat.ticks_ms())
    return {"now_showing": _now_showing()}


@api("/plugins/prev", ("POST",))
def plugins_prev(_request):
    _host().step(-1, _compat.ticks_ms())
    return {"now_showing": _now_showing()}


@api("/plugins/order", ("POST",))
def plugins_order(request):
    order = _body(request)["order"]
    current = _config().get("plugins", [])
    if not isinstance(order, list) or sorted(order) != sorted(current):
        raise ValueError("order must list every installed plugin once")
    _config().set("plugins", list(order))
    return plugins(request)


@api("/plugins/<name>")
def plugin_get(_request, name):
    name, err = _known(name)
    return err or _plugin_info(name)


@api("/plugins/<name>/show", ("POST",))
def plugin_show(_request, name):
    name, err = _known(name)
    if err:
        return err
    if not _host().show(name):
        return {"ok": False, "error": f"{name} failed to load"}, 500
    return {"ok": True, "now_showing": _now_showing()}


@api("/plugins/<name>/enable", ("POST",))
def plugin_enable(_request, name):
    name, err = _known(name)
    if err:
        return err
    cfg = _config()
    if name in cfg.get("quarantined", {}):
        return {"error": f"{name} is quarantined; un-quarantine it first"}, 409
    cfg.set("disabled_plugins", [p for p in cfg.get("disabled_plugins", []) if p != name])
    host = _host()
    if not host.safe_mode:
        _runtime.loader.load_all()
        if host.current is None:
            host.step(1, _compat.ticks_ms())
    return _plugin_info(name)


@api("/plugins/<name>/disable", ("POST",))
def plugin_disable(_request, name):
    name, err = _known(name)
    if err:
        return err
    cfg = _config()
    disabled = cfg.get("disabled_plugins", [])
    if name not in disabled:
        cfg.set("disabled_plugins", [*disabled, name])
    host = _host()
    showing = host.current is not None and host.current.name == name
    _runtime.loader.unload(name)
    if showing:
        host.plugin_removed(name)
    return _plugin_info(name)


@api("/plugins/<name>/unquarantine", ("POST",))
def plugin_unquarantine(_request, name):
    name, err = _known(name)
    if err:
        return err
    if not _runtime.loader.unquarantine(name):
        return {"error": f"{name} is not quarantined"}, 409
    host = _host()
    if not host.safe_mode:
        _runtime.loader.load(name)
        if host.current is None:
            host.step(1, _compat.ticks_ms())
    return _plugin_info(name)


# ---- settings ---------------------------------------------------------------

def _zones():
    try:
        import timezones

        return timezones.choices()
    except ImportError:
        return []


@api("/settings", ("GET", "POST"))
def settings(request):
    cfg = _config()
    reboot = False
    if request.method == "POST":
        body = _body(request)
        if "brightness" in body:
            cfg.set("brightness", _percent(body["brightness"]))
            _host().apply_brightness()
        if "volume" in body:
            cfg.set("volume", int(_percent(body["volume"]) * 127 / 100 + 0.5))
            _host().apply_volume()
        if "delay" in body:
            delay = int(body["delay"])
            if not 0 <= delay <= 86400:
                raise ValueError("delay must be 0-86400 seconds")
            cfg.set("delay", delay)
        if "hostname" in body:
            name = str(body["hostname"]).strip().lower()
            if not _wifi.valid_hostname(name):
                raise ValueError("hostname: letters, digits and '-' only, up to 32")
            if name != cfg.get("wifi_hostname"):
                cfg.set("wifi_hostname", name)
                reboot = True
        if "time_zone" in body:
            zone = str(body["time_zone"])
            keys = [k for k, _ in _zones()]
            if keys and zone not in keys:
                raise ValueError(f"unknown time zone {zone}")
            cfg.set("time_zone", zone)
        if "api_token" in body:
            token = body["api_token"]
            token = str(token).strip() if token else ""
            if len(token) > MAX_TOKEN or any(c in token for c in " ;,\"'\\"):
                raise ValueError("token: up to 64 characters, no spaces, quotes, ';' or ','")
            cfg.set("api_token", token or None)
        cfg.save()
    out = {
        "brightness": cfg.get("brightness", 60),
        "volume": _volume_percent(),
        "delay": cfg.get("delay", 30),
        "hostname": cfg.get("wifi_hostname") or _wifi.DEFAULT_HOSTNAME,
        "time_zone": cfg.get("time_zone", "uk"),
        "time_zones": [{"key": k, "label": v} for k, v in _zones()],
        "token_set": _token() is not None,
    }
    if reboot:
        out["reboot_required"] = True
    resp = reply(out)
    token = _token()
    if request.method == "POST" and token is not None:
        # the page that just set (or changed) the token keeps working
        resp.add_header("Set-Cookie", _set_cookie(token))
    return resp


def _set_cookie(token):
    return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"


# ---- wifi -------------------------------------------------------------------

@api("/wifi")
def wifi_get(_request):
    wifi = _wifi_mgr()
    if wifi is None:
        return error(503, "WiFi unavailable")
    out = wifi.status()
    out["audio_streaming"] = _bt_status()["streaming"]
    return out


@api("/wifi", ("POST",))
def wifi_save(request):
    body = _body(request)
    cfg = _config()
    ssid = str(body.get("ssid") or "").strip()
    if not 0 < len(ssid.encode("utf-8")) <= 32:
        raise ValueError("ssid must be 1-32 bytes")
    password = body.get("password")
    if password is None or password == "":
        # keep the stored password when just re-saving the same network
        password = cfg.get("wifi_password") if ssid == cfg.get("wifi_ssid") and password is None else None
    else:
        password = str(password)
        if not 8 <= len(password) <= 63 and len(password) != 64:
            raise ValueError("password must be 8-63 characters (or empty for an open network)")
    if "hostname" in body and body["hostname"]:
        name = str(body["hostname"]).strip().lower()
        if not _wifi.valid_hostname(name):
            raise ValueError("hostname: letters, digits and '-' only, up to 32")
        cfg.set("wifi_hostname", name)
    cfg.set("wifi_ssid", ssid)
    cfg.set("wifi_password", password)
    cfg.save(force=True)
    schedule_reboot()
    host = cfg.get("wifi_hostname") or _wifi.DEFAULT_HOSTNAME
    return {"ok": True, "rebooting": True, "ssid": ssid, "url": f"http://{host}.local"}


@api("/wifi/forget", ("POST",))
def wifi_forget(_request):
    cfg = _config()
    cfg.set("wifi_ssid", None)
    cfg.set("wifi_password", None)
    cfg.save(force=True)
    schedule_reboot()
    return {"ok": True, "rebooting": True}


async def _do_scan():
    global _scan_task
    try:
        import asyncio

        # let the 202 go out before the radio goes busy
        await asyncio.sleep(0.2)
        _feed()
        _wifi_mgr().scan()
        _feed()
    finally:
        _scan_task = None


def _feed():
    if _rt is not None and _rt.watchdog is not None:
        _rt.watchdog.feed()


@api("/wifi/scan", ("POST",))
def wifi_scan(_request):
    global _scan_task
    wifi = _wifi_mgr()
    if wifi is None or wifi.net is None:
        return error(503, "WiFi unavailable")
    out = {"scanning": True}
    if _bt_status()["streaming"]:
        out["warning"] = SCAN_WARNING
    if _scan_task is None and not wifi.scanning:
        wifi.scanning = True
        try:
            _scan_task = spawn(_do_scan())
        except Exception:
            wifi.scanning = False
            raise
    return out, 202


@api("/wifi/networks")
def wifi_networks(_request):
    wifi = _wifi_mgr()
    if wifi is None:
        return error(503, "WiFi unavailable")
    age = _compat.ticks_diff(_compat.ticks_ms(), wifi.scanned_at) if wifi.scanned_at is not None else None
    return {"scanning": wifi.scanning, "networks": wifi.networks, "age_ms": age, "current": wifi.ssid}


# ---- bluetooth --------------------------------------------------------------

@api("/bluetooth")
def bluetooth(_request):
    return _bt_status()


@api("/bluetooth/forget", ("POST",))
def bluetooth_forget(_request):
    _host().menu_forget_bt()
    return {"ok": True, **_bt_status()}


@api("/bluetooth/pairable", ("POST",))
def bluetooth_pairable(request):
    _host().menu_set_pairable(_bool(_body(request)["value"]))
    return _bt_status()


# ---- system -----------------------------------------------------------------

def _memory():
    import gc

    try:
        return {"free": gc.mem_free(), "used": gc.mem_alloc()}
    except AttributeError:
        return None


def _flash():
    import os

    try:
        st = os.statvfs(_runtime.path("/") or "/")
    except (OSError, AttributeError):
        return None
    return {"total": st[1] * st[2], "free": st[1] * st[3]}  # block counts are in f_frsize units


@api("/system")
def system(_request):
    import sys

    import gitmatrix

    impl = getattr(sys.implementation, "version", ())
    loader = _runtime.loader
    return {
        "version": gitmatrix.VERSION,
        "micropython": ".".join(str(v) for v in impl[:3]),
        "uptime_ms": _compat.ticks_diff(_compat.ticks_ms(), _rt.booted) if _rt is not None else None,
        "memory": _memory(),
        "flash": _flash(),
        "safe_mode": _host().safe_mode if _host() is not None else None,
        "quarantined": _config().get("quarantined", {}),
        "errors": [{"plugin": n, "hook": h, "error": t} for n, h, t in (loader.errors if loader else [])],
        "reboot_pending": _runtime.reboot_at is not None,
    }


@api("/system/logs")
def system_logs(_request):
    now = _compat.ticks_ms()
    return {"lines": [{"age_ms": _compat.ticks_diff(now, t), "text": text} for t, text in _log.recent()]}


@api("/system/reboot", ("POST",))
def system_reboot(_request):
    schedule_reboot()
    return {"ok": True, "rebooting": True}


# ---- pages ------------------------------------------------------------------

def _our_hosts():
    wifi = _wifi_mgr()
    hosts = [_wifi.AP_IP]
    if wifi is not None:
        hosts.append(wifi.hostname + ".local")
        if wifi.ip:
            hosts.append(wifi.ip)
    return hosts


def catchall(request):
    path = request.path
    wifi = _wifi_mgr()
    host = request.headers.get("host", "").split(":")[0]
    if wifi is not None and wifi.ap_on and host not in _our_hosts():
        # captive portal: phones probe some well-known URL; send them to setup
        return _response("", 302, "text/plain", {"Location": f"http://{_wifi.AP_IP}/wifi.html"})
    if path.startswith(API + "/") or path == API:
        return error(404, "no such endpoint")
    if ".." in path or "//" in path or "\\" in path:
        return _response("Not found", 404, "text/plain")
    if path in ("", "/"):
        path = "/index.html"
    from phew.server import FileResponse

    file = _runtime.path("/html" + path)
    if not _compat.exists(file) and "." not in path.rsplit("/", 1)[-1]:
        file += ".html"
    if not _compat.exists(file):
        return _response("Not found", 404, "text/plain")
    resp = FileResponse(file)
    if file.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache"
        token = _token()
        if token is not None:
            resp.headers["Set-Cookie"] = _set_cookie(token)
    return resp


def dispatch(request):
    """Route a request the way phew's server does (used by tests)."""
    server = _server()
    route = server._match_route(request)  # noqa: SLF001 - phew has no public matcher
    if route:
        return route.call_handler(request)
    return server.catchall_handler(request)


def setup(rt=None):
    """Register routes with phew. Safe to call again (tests, REPL)."""
    global _rt
    _rt = rt
    from phew import logging

    server = _server()
    logging.log_file = None             # never write log.txt (flash writes stall audio)
    logging.log = _log.phew_log
    logging.disable_logging_types(logging.LOG_INFO | logging.LOG_DEBUG)
    # start from a clean table so re-running setup() doesn't double up
    server.clear_routes()
    for path, handler, methods in _ROUTES:
        server.add_route(path, handler, methods)
    server.set_callback(catchall)
    return server


async def start(rt, port=80):
    server = setup(rt)
    try:
        await server.start("0.0.0.0", port)
    except OSError as e:
        _log.log(f"web: could not start ({_compat.error_text(e)})")
        return False
    _log.log(f"web: listening on port {port}")
    return True
