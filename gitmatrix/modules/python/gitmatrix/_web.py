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

from gitmatrix import _audio, _compat, _events, _flash, _log, _notify, _runtime, _sounds, _wifi

API = "/api/v1"
COOKIE = "gm_token"
REBOOT_DELAY_MS = 1500
SCAN_WARNING = "audio may stutter while scanning"
MAX_TOKEN = 64
MAX_BODY = 64 * 1024        # any request body, checked before it is read
MAX_POLLERS = 2             # /events long-polls held open at once
MAX_WAIT_S = 30             # longest /events long-poll
DEFAULT_WAIT_S = 25
RETRY_MS = 1000             # suggested back-off when the long-poll slots are full

_rt = None          # the boot Runtime
_scan_task = None
_sound_task = None  # POST /sound
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


def _normalize(out):
    if isinstance(out, tuple):
        return reply(out[0], out[1])
    return out if not isinstance(out, (dict, list)) else reply(out)


def deferred(coro):
    """Answer later: phew awaits `coro` in this connection's task (long-polls)."""
    from phew.server import Deferred

    return Deferred(coro)


async def _finish(coro):
    try:
        out = await coro
    except (ValueError, TypeError, KeyError) as e:
        return error(400, _compat.error_text(e))
    return _normalize(out)


def api(path, methods=("GET",)):
    """Register an /api/v1 handler: auth, JSON replies, errors as JSON. A
    handler may return deferred(coro) to reply asynchronously."""
    def wrap(fn):
        def handler(request, **params):
            if not authorized(request):
                return error(401, "unauthorized")
            try:
                out = fn(request, **params)
            except (ValueError, TypeError, KeyError) as e:
                return error(400, _compat.error_text(e))
            if type(out).__name__ == "Deferred":
                return deferred(_finish(out.coro))
            return _normalize(out)
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


def audio_gate(body):
    """User-initiated flash writes while Bluetooth audio streams need the
    caller's OK (pause_audio): the sink stops for the write and the phone has
    to reconnect. None to go ahead, else the 409 reply."""
    if _flash.streaming() and not _bool(body.get("pause_audio", False)):
        return {"error": _flash.AUDIO_WARNING, "audio_streaming": True}, 409
    return None


def schedule_reboot(delay_ms=REBOOT_DELAY_MS):
    """Reboot from housekeeping once this response has gone out."""
    _runtime.reboot_at = _compat.ticks_add(_compat.ticks_ms(), delay_ms)
    _log.log("system: reboot requested")


def schedule_wifi_restart(delay_ms=REBOOT_DELAY_MS):
    """Re-apply WiFi settings from housekeeping once this response has gone out."""
    _runtime.wifi_restart_at = _compat.ticks_add(_compat.ticks_ms(), delay_ms)
    _log.log("wifi: restart requested")


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
    info = {
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
    info.update(_store_info(name))
    return info


def _store_info(name):
    from gitmatrix import _loader, _store

    manifest = _loader.read_manifest(name)
    installed = _store.active()
    store = _runtime.store
    has_config = _compat.exists(_store.live_dir(name) + "/config.html")
    return {
        "version": manifest.get("version") if isinstance(manifest.get("version"), str) else None,
        "installed": name in installed,
        "builtin": name not in installed,
        "update_available": bool(store is not None and store.update_available(name, installed)),
        "has_settings": bool(manifest.get("settings")) or has_config,
        "has_config_html": has_config,
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


def _mode():
    host = _host()
    if host is None:
        return None
    from gitmatrix import _host as host_mod

    return host_mod.MODE_NAMES[host.mode]


def _encoder_status():
    host = _host()
    bus = _runtime.events
    src = host.input if host is not None else None
    return {
        "position": bus.position if bus is not None else 0,
        "seq": src.seq if src is not None else 0,
        "last": bus.state.get(_events.ENCODER) if bus is not None else None,
    }


def _events_status():
    bus = _runtime.events
    if bus is None:
        return None
    return {"seq": bus.seq, "boot": bus.boot, "pollers": bus.waiters}


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

    showing = _now_showing()
    return {
        "version": gitmatrix.VERSION,
        "firmware": gitmatrix.VERSION,
        "uptime_ms": _compat.ticks_diff(_compat.ticks_ms(), _rt.booted) if _rt is not None else None,
        "plugin": showing["name"] if showing else None,
        "mode": _mode(),
        "now_showing": showing,
        "menu_open": host is not None and host.mode != 0,
        "safe_mode": host.safe_mode if host is not None else None,
        "plugins": len(host.plugins()) if host is not None else 0,
        "delay": cfg.get("delay", 30),
        "brightness": cfg.get("brightness", 60),
        "volume": _volume_percent(),
        "wifi": wifi.status() if wifi is not None else None,
        "bluetooth": _bt_status(),
        "input": _input_status(),
        "encoder": _encoder_status(),
        "events": _events_status(),
        "memory": _memory(),
        "flash": _flash_space(),
        "token_required": _token() is not None,
        "notifications": _notifier().status() if _notifier() is not None else None,
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


SHOW_FIELDS = ("data", "duration", "priority", "id", "ttl", "sound", "sound_url", "sound_data", "repeat")


@api("/plugins/<name>/show", ("POST",))
def plugin_show(request, name):
    name, err = _known(name)
    if err:
        return err
    body = _body(request)
    if body.get("data") is not None or body.get("duration") is not None:
        # for a while (and/or with data): the notification path pre-empts the
        # rotation and hands back to whatever was showing
        n = _notifier()
        if n is None:
            return error(503, "notifications are not running")
        req = {k: body[k] for k in body if k in SHOW_FIELDS}
        req["plugin"] = name
        try:
            note = n.submit(req)
        except _notify.NotifyError as e:
            return error(e.status, _compat.error_text(e))
        return {"ok": True, "id": note.id, "queued": len(n.queue), "temporary": True}, 202
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
        cfg.set("disabled_plugins", disabled + [name])
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


# ---- plugin store -----------------------------------------------------------

def _store():
    from gitmatrix import _store as store_mod

    store = _runtime.store
    if store is None:
        store = store_mod.Store()
    return store, store_mod


@api("/store")
def store_get(_request):
    store, _ = _store()
    return store.catalogue()


@api("/store/refresh", ("POST",))
def store_refresh(_request):
    store, _ = _store()
    wifi = _wifi_mgr()
    if wifi is None or not wifi.connected():
        return {"error": "not connected to the internet"}, 409
    wait = store.refresh_wait_ms()
    if store.refreshing or wait:
        return {"refreshing": store.refreshing, "retry_ms": wait}, 429 if wait else 202
    store.refreshing = True
    spawn(store.refresh())
    return {"refreshing": True}, 202


@api("/store/install", ("GET", "POST"))
def store_install(request):
    store, store_mod = _store()
    if request.method == "GET":
        return {"install": store.progress}
    body = _body(request)
    name = str(body["name"])
    if not _compat.valid_name(name):
        raise ValueError("bad plugin name")
    streaming = _flash.streaming()
    try:
        progress = store.begin(name, pause_audio=_bool(body.get("pause_audio", False)),
                               queue=_bool(body.get("queue", False)))
    except store_mod.StoreError as e:
        return _store_error(e, streaming)
    spawn(store.install(name))
    out = {"install": progress}
    if streaming:
        out["warning"] = _flash.AUDIO_WARNING
    return out, 202


def _store_error(e, streaming):
    out = {"error": str(e)}
    if e.status == 409 and streaming and str(e) == _flash.AUDIO_WARNING:
        out["audio_streaming"] = True
    return out, e.status


@api("/plugins/<name>/remove", ("POST",))
def plugin_remove(request, name):
    store, store_mod = _store()
    body = _body(request)
    streaming = _flash.streaming()
    try:
        return store.remove(name, keep_data=_bool(body.get("keep_data", True)),
                            pause_audio=_bool(body.get("pause_audio", False)))
    except store_mod.StoreError as e:
        return _store_error(e, streaming)


# ---- plugin settings --------------------------------------------------------

def _plugin_settings_reply(name):
    from gitmatrix import _state

    s = _state.settings(name)
    schema = _state.public_schema(s.schema)
    values = s.redacted()
    for field in schema:
        if field["type"] == "secret":
            field["set"] = bool(values.get(field["key"]))
    info = _store_info(name)
    return {
        "name": name,
        "title": _plugin_info(name)["title"],
        "schema": schema,
        "values": {k: v for k, v in values.items() if not s.is_secret(k)},
        "has_config_html": info["has_config_html"],
        "config_url": f"/config?plugin={name}" if info["has_config_html"] else None,
    }


def _reload_plugin(name):
    """Settings changed: call on_settings, else reload the module."""
    loader = _runtime.loader
    host = _host()
    if loader is None:
        return
    plugin = loader.plugins.get(name)
    if plugin is None or plugin.module is None:
        return
    if plugin.hook("on_settings") is not None:
        loader.call(plugin, "on_settings")
        return
    showing = host is not None and host.current is plugin
    if showing:
        host._enter(None, _compat.ticks_ms())  # noqa: SLF001 - on_hide, then reload
    loader.unload(name)
    if loader.load(name) is not None and showing:
        host.show(name)
    elif showing:
        host.step(1, _compat.ticks_ms())


@api("/plugins/<name>/settings", ("GET", "POST"))
def plugin_settings(request, name):
    name, err = _known(name)
    if err:
        return err
    from gitmatrix import _state

    if request.method == "POST":
        body = _body(request)
        blocked = audio_gate(body)
        if blocked:
            return blocked
        body.pop("pause_audio", None)
        with _flash.pause():
            _state.settings(name).update(body, strict=True)
        _reload_plugin(name)
    return _plugin_settings_reply(name)


# ---- Art Matrix config.html pages (/config?plugin=name) ---------------------
#
# As on Art Matrix: GET renders the plugin's config.html with `plugin` (its
# name) and `placeholder` (its module) in scope; POST passes the form to the
# plugin's process_config(form), or saves it as /data/<name>/config.json for
# plugin_config(). A form field "reset" deletes the saved config. Also as on
# Art Matrix, <form data-autosave> saves on change (/static/config.js) and a
# "Reset to defaults" button is added for plugins without process_config.

def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def render(template, params):
    """{{ expr }} from phew's templates, synchronously; plain names are escaped."""
    out = []
    pos = 0
    while True:
        start = template.find("{{", pos)
        if start < 0:
            out.append(template[pos:])
            return "".join(out)
        end = template.find("}}", start + 2)
        if end < 0:
            out.append(template[pos:])
            return "".join(out)
        out.append(template[pos:start])
        expr = template[start + 2:end].strip()
        try:
            if expr in params:
                value = _escape(params[expr])
            else:
                value = eval(expr, {}, params)  # noqa: S307 - the plugin's own template, as phew
                value = "" if value is None else str(value)
        except Exception as e:  # noqa: BLE001 - a broken template shouldn't take the page down
            _log.log(f"config: {{{{ {expr} }}}} failed: {_compat.error_text(e)}")
            value = ""
        out.append(value)
        pos = end + 2


_CONFIG_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - GitMatrix</title><link rel="stylesheet" href="/static/app.css"></head>
<body><header class="top"><a class="brand" href="/">GitMatrix</a>
<nav><a href="/">Home</a><a href="/discover.html">Discover</a><a href="/settings.html">Settings</a></nav></header>
<main><p><a href="/plugin.html?name={name}">&larr; {title}</a></p>
<section class="card config-html">{body}</section>{reset}</main>
<script src="/static/app.js"></script><script src="/static/config.js"></script></body></html>"""

_RESET_FORM = """<section class="card"><form method="post" action="/config?plugin={name}"
onsubmit="return confirm('Reset {name} to its default settings?')">
<input type="hidden" name="reset" value="1"><button type="submit" class="danger">Reset to defaults</button>
</form></section>"""


def _module_for(name):
    """The plugin's module: the loaded one, else a throwaway import."""
    loader = _runtime.loader
    plugin = loader.plugins.get(name) if loader is not None else None
    if plugin is not None and plugin.module is not None:
        return plugin.module, False
    from gitmatrix import _loader

    try:
        return _loader._import(name), True  # noqa: SLF001
    except Exception as e:  # noqa: BLE001 - render without it
        _log.log(f"config: import {name} failed {_compat.error_text(e)}")
        return None, False


def _drop_module(name):
    import sys

    from gitmatrix import _loader

    sys.modules.pop(_loader._module_key(name), None)  # noqa: SLF001


def config_page(request):
    if not authorized(request) and request.method != "GET":
        return _response("Unauthorized", 401, "text/plain")
    name = request.query.get("plugin", "")
    from gitmatrix import _store

    if not _compat.valid_name(name) or name not in _config().get("plugins", []):
        return _response("No such plugin", 404, "text/plain")
    path = _store.live_dir(name) + "/config.html"
    if request.method == "POST":
        if not authorized(request):
            return _response("Unauthorized", 401, "text/plain")
        form = dict(request.form) if isinstance(request.form, dict) else {}
        if _flash.streaming() and not _bool(form.pop("pause_audio", False)):
            return _audio_confirm_page(name, form)
        form.pop("pause_audio", None)
        module, temporary = _module_for(name)
        try:
            with _flash.pause():
                _config_save(name, form, module)
        except Exception as e:  # noqa: BLE001 - the plugin's own code
            _log.log(f"config: {name} process_config failed {_compat.error_text(e)}")
            return _response(f"Couldn't save: {_escape(_compat.error_text(e))}", 400, "text/plain")
        finally:
            if temporary:
                _drop_module(name)
        _reload_plugin(name)
        return _response("", 303, "text/plain", {"Location": f"/config?plugin={name}"})
    try:
        with open(path) as f:
            template = f.read()
    except OSError:
        return _response("This plugin has no config page", 404, "text/plain")
    module, temporary = _module_for(name)
    try:
        body = render(template, {"plugin": name, "placeholder": module})
        show_reset = not hasattr(module, "process_config")
    finally:
        if temporary:
            _drop_module(name)
    return _config_html(name, body, show_reset=show_reset)


def _config_save(name, form, module):
    if "reset" in form:
        _compat.remove_tree(_runtime.data_dir(name) + "/config.json")
    elif module is not None and hasattr(module, "process_config"):
        module.process_config(form)
    else:
        from gitmatrix import _state

        path = _runtime.data_dir(name) + "/config.json"
        # autosave can post the same form again: don't rewrite flash for it
        if not _compat.exists(path) or _state._read_json(path, {}) != form:  # noqa: SLF001
            _state._write_json(path, form)  # noqa: SLF001


def _audio_confirm_page(name, form):
    """A config.html form posted while audio streams: say so, and offer to save
    anyway (the same fields again, plus pause_audio)."""
    fields = "".join(f'<input type="hidden" name="{_escape(k)}" value="{_escape(v)}">' for k, v in form.items())
    body = (f'<h2>Bluetooth audio is playing</h2><p class="warn">{_flash.AUDIO_WARNING}.</p>'
            f'<form method="post" action="/config?plugin={name}">{fields}'
            '<input type="hidden" name="pause_audio" value="1">'
            '<button type="submit">Save anyway</button> '
            f'<a class="button secondary" href="/config?plugin={name}">Cancel</a></form>')
    return _config_html(name, body, 409)


def _config_html(name, body, status=200, show_reset=False):
    title = _escape(_plugin_info(name)["title"])
    headers = {"Cache-Control": "no-store"}
    token = _token()
    if token is not None:
        headers["Set-Cookie"] = _set_cookie(token)
    return _response(_CONFIG_PAGE.format(title=title, name=name, body=body,
                                         reset=_RESET_FORM.format(name=name) if show_reset else ""), status, "text/html", headers)


_ROUTES.append(("/config", config_page, ["GET", "POST"]))


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
    restart_wifi = False
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
                restart_wifi = True
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
        _alert_settings(cfg, body)
        if _flash.can_write():
            cfg.save()      # else housekeeping saves it once playback stops
        if restart_wifi:
            schedule_wifi_restart()
    out = {
        "brightness": cfg.get("brightness", 60),
        "volume": _volume_percent(),
        "delay": cfg.get("delay", 30),
        "hostname": cfg.get("wifi_hostname") or _wifi.DEFAULT_HOSTNAME,
        "time_zone": cfg.get("time_zone", "uk"),
        "time_zones": [{"key": k, "label": v} for k, v in _zones()],
        "token_set": _token() is not None,
    }
    out.update(_alert_values(cfg))
    if restart_wifi:
        out["restarting_wifi"] = True
    resp = reply(out)
    token = _token()
    if request.method == "POST" and token is not None:
        # the page that just set (or changed) the token keeps working
        resp.add_header("Set-Cookie", _set_cookie(token))
    return resp


ALERT_FLAGS = ("alert_sound", "quiet_hours_enabled", "quiet_bypass_high", "alert_interrupt_audio")


def _hhmm(value):
    text = str(value).strip()
    h, sep, m = text.partition(":")
    if not sep or not h.isdigit() or not m.isdigit() or len(m) != 2 or int(h) > 23 or int(m) > 59:
        raise ValueError("quiet hours: use HH:MM")
    return f"{int(h):02d}:{m}"


def _alert_settings(cfg, body):
    for key in ALERT_FLAGS:
        if key in body:
            cfg.set(key, _bool(body[key]))
    if "quiet_start" in body or "quiet_end" in body:
        start, end, _ = _notify.quiet_window(cfg)
        start = _hhmm(body["quiet_start"]) if "quiet_start" in body else f"{start // 60:02d}:{start % 60:02d}"
        end = _hhmm(body["quiet_end"]) if "quiet_end" in body else f"{end // 60:02d}:{end % 60:02d}"
        cfg.set("quiet_hours", (start, end))
    if "quiet_days" in body:
        days = body["quiet_days"]
        if isinstance(days, str):
            days = [d for d in days.split(",") if d.strip()]
        days = sorted({int(d) for d in days})
        if any(not 0 <= d <= 6 for d in days):
            raise ValueError("quiet_days: 0 (Monday) to 6 (Sunday)")
        cfg.set("quiet_days", days)


def _alert_values(cfg):
    start, end, days = _notify.quiet_window(cfg)
    out = {k: bool(cfg.get(k, k == "alert_sound")) for k in ALERT_FLAGS}
    out["quiet_start"] = f"{start // 60:02d}:{start % 60:02d}"
    out["quiet_end"] = f"{end // 60:02d}:{end % 60:02d}"
    out["quiet_days"] = list(days)
    out["quiet_now"] = _notify.in_quiet_hours(cfg)
    return out


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
    out = {"ok": True, "restarting_wifi": True, "ssid": ssid}
    if _flash.streaming():
        out["warning"] = _flash.AUDIO_WARNING
    with _flash.pause():
        cfg.save(force=True)
    schedule_wifi_restart()
    host = cfg.get("wifi_hostname") or _wifi.DEFAULT_HOSTNAME
    out["url"] = f"http://{host}.local"
    return out


@api("/wifi/forget", ("POST",))
def wifi_forget(_request):
    cfg = _config()
    cfg.set("wifi_ssid", None)
    cfg.set("wifi_password", None)
    out = {"ok": True, "restarting_wifi": True}
    if _flash.streaming():
        out["warning"] = _flash.AUDIO_WARNING
    with _flash.pause():
        cfg.save(force=True)
    schedule_wifi_restart()
    return out


async def _do_scan():
    global _scan_task
    try:
        import asyncio

        # let the 202 go out before the radio (and this loop) goes busy
        await asyncio.sleep(0.2)
        _wifi_mgr().scan(_feed)
    finally:
        _scan_task = None


def _feed():
    if _rt is not None and _rt.watchdog is not None:
        _rt.watchdog.feed()


@api("/wifi/scan", ("POST",))
def wifi_scan(_request):
    # WLAN.scan() can't yield (see _wifi), so rescans are explicit and rate-limited
    global _scan_task
    wifi = _wifi_mgr()
    if wifi is None or wifi.net is None:
        return error(503, "WiFi unavailable")
    if _scan_task is not None or wifi.scanning:
        return {"scanning": True}, 202
    wait = wifi.scan_wait_ms()
    if wait:
        return reply({"error": f"Just scanned: try again in {(wait + 999) // 1000} s", "retry_ms": wait}, 429)
    out = {"scanning": True}
    if _bt_status()["streaming"]:
        out["warning"] = SCAN_WARNING
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
    reply = {"ok": True}
    reply.update(_bt_status())
    return reply


@api("/bluetooth/pairable", ("POST",))
def bluetooth_pairable(request):
    value = _bool(_body(request)["value"])
    if not _audio.pairable_supported():
        return {"error": "this firmware can't change pairability (bt_audio has no set_pairable)"}, 501
    _host().menu_set_pairable(value)
    return _bt_status()


# ---- system -----------------------------------------------------------------

# ---- notifications ------------------------------------------------------------

def _notifier():
    return _runtime.notifier


@api("/notify", ("GET", "POST", "DELETE"))
def notify(request):
    n = _notifier()
    if n is None:
        return error(503, "notifications are not running")
    if request.method == "GET":
        return n.status()
    if request.method == "DELETE":
        nid = request.query.get("id") or (request.data.get("id") if isinstance(request.data, dict) else None)
        return {"dismissed": n.dismiss(nid or None)}
    try:
        note = n.submit(request.data)
    except _notify.NotifyError as e:
        return error(e.status, _compat.error_text(e))
    return {"id": note.id, "queued": len(n.queue)}, 202


# ---- MQTT / Home Assistant -------------------------------------------------------

MQTT_FIELDS = ("enabled", "host", "port", "user", "password", "tls", "discovery", "discovery_prefix", "base_topic")


def _mqtt_text(value, field, limit):
    text = str(value).strip() if value is not None else ""
    if len(text) > limit or any(c in text for c in " \t\r\n\x00"):
        raise ValueError(f"{field}: up to {limit} characters, no spaces")
    return text or None


def _mqtt_settings(cfg, body):
    from gitmatrix import _mqtt

    for key in body:
        if key not in MQTT_FIELDS:
            raise ValueError(f"unknown field: {key}")
    for key in ("enabled", "tls", "discovery"):
        if key in body:
            cfg.set("mqtt_" + key, _bool(body[key]))
    if "host" in body:
        cfg.set("mqtt_host", _mqtt_text(body["host"], "host", _mqtt.MAX_HOST))
    if "port" in body:
        port = int(body["port"]) if body["port"] not in (None, "") else None
        if port is not None and not 0 < port < 65536:
            raise ValueError("port must be 1-65535")
        cfg.set("mqtt_port", port)
    if "user" in body:
        cfg.set("mqtt_user", _mqtt_text(body["user"], "user", _mqtt.MAX_SECRET))
    if "password" in body:      # write-only; "" or null clears it
        password = body["password"]
        password = str(password) if password else ""
        if len(password) > _mqtt.MAX_SECRET:
            raise ValueError(f"password: up to {_mqtt.MAX_SECRET} characters")
        cfg.set("mqtt_password", password or None)
    for key in ("discovery_prefix", "base_topic"):
        if key in body:
            value = str(body[key]).strip() if body[key] else ""
            if value and not _mqtt.valid_topic(value):
                raise ValueError(f"{key}: a topic without spaces, '+', '#' or a leading/trailing '/'")
            cfg.set("mqtt_" + key, value or None)
    if cfg.get("mqtt_enabled") and not cfg.get("mqtt_host"):
        raise ValueError("host: set the broker's address to turn MQTT on")


def _mqtt_values(cfg):
    from gitmatrix import _mqtt

    s = _mqtt.settings(cfg)
    client = _runtime.mqtt
    tls = bool(cfg.get("mqtt_tls", False))
    return {
        "enabled": bool(cfg.get("mqtt_enabled", False)),
        "host": cfg.get("mqtt_host"),
        "port": cfg.get("mqtt_port"),           # null: the default for tls
        "default_port": 8883 if tls else 1883,
        "user": cfg.get("mqtt_user"),
        "password_set": bool(cfg.get("mqtt_password")),
        "tls": tls,
        "discovery": bool(cfg.get("mqtt_discovery", True)),
        "discovery_prefix": cfg.get("mqtt_discovery_prefix") or "homeassistant",
        "base_topic": cfg.get("mqtt_base_topic"),   # null: default_base_topic
        "default_base_topic": "gitmatrix/" + _mqtt.hostname(cfg),
        "topic": s["base"] if s else cfg.get("mqtt_base_topic") or "gitmatrix/" + _mqtt.hostname(cfg),
        "status": client.status() if client is not None else {"state": "unavailable", "connected": False},
    }


@api("/mqtt", ("GET", "POST"))
def mqtt(request):
    cfg = _config()
    if request.method == "POST":
        before = {k: v for k, v in cfg.values.items() if k.startswith("mqtt_")}
        try:
            _mqtt_settings(cfg, _body(request))
        except (ValueError, TypeError):
            cfg.values.update(before)       # all or nothing
            raise
        if _flash.can_write():
            cfg.save()      # else housekeeping saves it once playback stops
        if _runtime.mqtt is not None:
            _runtime.mqtt.reconfigure()     # applied in place: no reboot
    return _mqtt_values(cfg)


# ---- sounds -------------------------------------------------------------------

@api("/sounds")
def sounds_list(_request):
    return {
        "sounds": _sounds.listing(),
        "pending": _sounds.pending(),
        "used": _sounds.custom_total(),
        "max_size": _sounds.MAX_CUSTOM,
        "max_total": _sounds.MAX_CUSTOM_TOTAL,
        "playing": _sounds.playing is not None,
        "last_result": _sounds.last_result,
    }


def _upload_bytes(request):
    if request.body:
        return request.body
    data = request.data if isinstance(request.data, dict) else {}
    encoded = data.get("data")
    if not isinstance(encoded, str):
        raise ValueError("send the WAV as the body (audio/wav) or as base64 in 'data'")
    import binascii
    try:
        return binascii.a2b_base64(encoded)
    except (ValueError, binascii.Error) as e:
        raise ValueError("data: bad base64") from e


@api("/sounds/<name>", ("GET", "POST", "DELETE"))
def sound_item(request, name):
    if request.method == "GET":
        for item in _sounds.listing():
            if item["name"] == name:
                return item
        return error(404, f"no sound called {name}")
    try:
        if request.method == "DELETE":
            if not _sounds.delete(name):
                return error(404, f"no sound called {name}")
            return {"deleted": name}
        result = _sounds.save(name, _upload_bytes(request))
    except _sounds.SoundError as e:
        return error(e.status, _compat.error_text(e))
    except OSError as e:
        return error(500, f"could not save: {_compat.error_text(e)}")
    if result == "queued":
        return {"name": name, "queued": True, "message": "saved once Bluetooth audio stops"}, 202
    return {"name": name, "saved": True}


@api("/sounds/<name>/play", ("POST",))
def sound_play(_request, name):
    path = _sounds.find(name)
    if path is None:
        return error(404, f"no sound called {name}")
    if _flash.streaming():
        return {"played": False, "reason": "Bluetooth audio is streaming"}
    if _sounds.playing is not None:
        return {"played": False, "reason": "a sound is already playing"}
    spawn(_sounds.play(path, _config()))
    return {"played": True}


SOUND_FIELDS = ("sound", "sound_url", "sound_data", "priority", "force")


def _sound_busy():
    task = _sound_task
    busy = task is not None and not getattr(task, "done", lambda: False)()
    return busy or _sounds.playing is not None


async def _play_sound(source, url, high, force):
    if url is not None:
        try:
            source = await _notify._fetch(url, _sounds.MAX_URL_BYTES)  # noqa: SLF001
            _sounds.parse_wav(source)
        except (OSError, ValueError) as e:
            _sounds.last_result = f"error: {_compat.error_text(e)}"
            _log.log(f"sound: {url}: {_sounds.last_result}")
            return
    await _sounds.alert(source, high, _config(), force)


@api("/sound", ("POST", "DELETE"))
def sound(request):
    """Play a sound now, under the same rules as a notification's sound."""
    global _sound_task
    if request.method == "DELETE":
        task, _sound_task = _sound_task, None
        stopped = task is not None and not getattr(task, "done", lambda: False)()
        if stopped:
            task.cancel()
        return {"stopped": stopped}
    return play_request(_body(request))


def play_request(body):
    """POST /sound's body -> (reply, status). Shared with MQTT's <base>/sound."""
    global _sound_task
    for key in body:
        if key not in SOUND_FIELDS:
            raise ValueError(f"unknown field: {key}")
    given = [k for k in ("sound", "sound_url", "sound_data") if body.get(k) is not None]
    if len(given) != 1:
        raise ValueError("give one of sound, sound_url or sound_data")
    priority = body.get("priority", "normal")
    if priority not in ("normal", "high"):
        raise ValueError("priority must be normal or high")
    high = priority == "high"
    force = _bool(body.get("force", False))
    source = url = None
    try:
        if body.get("sound") is not None:
            source = _sounds.find(str(body["sound"]))
            if source is None:
                return {"error": f"no sound called {body['sound']}"}, 404
        elif body.get("sound_data") is not None:
            source = _notify._b64(body["sound_data"], _sounds.MAX_INLINE, "sound_data")  # noqa: SLF001
            _sounds.parse_wav(source)
        else:
            url = _notify._url(body["sound_url"], "sound_url")  # noqa: SLF001
    except _notify.NotifyError as e:
        return {"error": _compat.error_text(e)}, e.status
    policy = _notify.sound_policy(_config(), high, _flash.streaming(), force=force)
    if policy not in ("play", "interrupt"):
        _sounds.last_result = policy
        return {"played": False, "result": policy}, 200
    if _sound_busy():
        return {"played": False, "result": "busy"}, 200
    _sound_task = spawn(_play_sound(source, url, high, force))
    return {"played": True, "result": policy}, 202


# ---- events -------------------------------------------------------------------

def _since(value):
    if value is None or value == "":
        return None
    return int(value)


async def _long_poll(bus, since, kinds, timeout_ms):
    await bus.wait(since, kinds, timeout_ms)
    return bus.read(since, kinds)


@api("/events")
def events(request):
    """The event log, or a long-poll for what comes after `since`."""
    bus = _runtime.events
    if bus is None:
        return error(503, "events are not running")
    q = request.query
    kinds = _events.parse_types(q.get("types"))
    since = _since(q.get("since"))
    timeout = float(q.get("timeout", DEFAULT_WAIT_S))
    timeout = max(0.0, min(float(MAX_WAIT_S), timeout))
    if since is None or timeout == 0 or bus.pending(since, kinds):
        return bus.read(since, kinds)
    if bus.waiters >= MAX_POLLERS:
        return {"error": "too many long-polls", "retry_ms": RETRY_MS}, 429
    return deferred(_long_poll(bus, since, kinds, int(timeout * 1000)))


def body_limit(request):
    """Largest body phew may read for this request (sound uploads are bigger)."""
    path = request.path
    if (request.method == "POST" and path.startswith(API + "/sounds/") and not path.endswith("/play")
            and not request.headers.get("content-type", "").startswith("application/json")):
        return _sounds.MAX_CUSTOM + 1024
    return MAX_BODY


def _memory():
    import gc

    try:
        return {"free": gc.mem_free(), "used": gc.mem_alloc()}
    except AttributeError:
        return None


def _flash_space():
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
        "flash": _flash_space(),
        "audio_pauses": _flash.pauses,
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
    server.body_limit = body_limit
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
