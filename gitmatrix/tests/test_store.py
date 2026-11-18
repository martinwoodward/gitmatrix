# The plugin store: index fetch/cache, transactional install/update/remove,
# boot reconciliation after a power cut, the Bluetooth audio rule, settings
# with secrets, and the store/settings/config API.

import asyncio
import json
import os

import pytest
import stubs
from conftest import make_plugin, set_plugins

from gitmatrix import _flash, _runtime, _state, _store, _web

GOOD = "def update():\n    pass\n"


class PowerCut(BaseException):
    """Stands in for pulling the plug: nothing catches it."""


class Remote:
    """A fake raw.githubusercontent.com serving one plugin repo."""

    def __init__(self, monkeypatch):
        self.files = {}
        self.index = {"plugins": {}, "details": {}}
        self.gets = []
        monkeypatch.setattr(_store, "http_get", self.get)

    def path(self, rel):
        return _store.base_path() + "/" + rel

    def publish(self, name, files, version="1.0.0", title=None, size=None, **extra):
        files = {k: v.encode() if isinstance(v, str) else v for k, v in files.items()}
        manifest = {"name": title or name.title(), "version": version,
                    "hash": _store.package_hash(list(files.items())),
                    "description": f"{name} plugin", "author": "me", "category": "Information",
                    "requires_internet": False,
                    "urls": [[f"plugins/{name}/{rel}", rel] for rel in files], **extra}
        self.files[self.path(f"{name}/package.json")] = json.dumps(manifest).encode()
        for rel, data in files.items():
            self.files[self.path(f"{name}/{rel}")] = data
        self.index["plugins"][name] = manifest["name"]
        self.index["details"][name] = {
            "description": manifest["description"], "author": "me", "category": "Information",
            "hash": manifest["hash"], "version": version, "files": len(files),
            "size": size if size is not None else sum(len(v) for v in files.values()),
            "tags": ["demo"], "settings": "settings" in extra,
        }
        self.files[self.path("index.json")] = json.dumps(self.index).encode()
        return manifest

    async def get(self, path, max_bytes=_store.MAX_FILE_BYTES, file=None):
        self.gets.append(path)
        if path not in self.files:
            raise OSError("HTTP 404")
        data = self.files[path]
        if file:
            with open(file, "wb") as f:
                f.write(data)
            return None
        return data


@pytest.fixture
def store(rig, monkeypatch):
    set_plugins(rig.root, ["clock"], wifi_ssid="home", wifi_password="secret123")
    rig.net = stubs.FakeNetwork()
    rig.remote = Remote(monkeypatch)
    rig.boot()
    rig.store = rig.rt.store
    return rig


def run(coro):
    # not asyncio.run(): that leaves no current loop for phew's setup
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def install(rig, name, **kw):
    rig.store.begin(name, **kw)
    run(rig.store.install(name))
    return rig.store.progress


def live(rig, name):
    return rig.root / "lib" / "plugins" / name


# ---- index ------------------------------------------------------------------

def test_parse_index_normalises_and_drops_bad_entries():
    out = _store.parse_index({
        "plugins": {"good": "Good", "1bad": "Bad", "no_title": "", "evil/../x": "X"},
        "details": {"good": {"description": "d" * 500, "tags": ["a", 3, "b"], "size": 10,
                             "files": -1, "settings": True}},
    })
    assert list(out) == ["good"]
    g = out["good"]
    assert len(g["description"]) == 200 and g["tags"] == ["a", "b"]
    assert g["size"] == 10 and g["files"] is None and g["settings"] is True
    with pytest.raises(ValueError):
        _store.parse_index({"nope": 1})


@pytest.mark.parametrize("urls", [
    [], [["plugins/x/a.py", "a.py"]],                       # no __init__.py
    [["plugins/x/__init__.py", "__init__.py"], ["plugins/y/a.py", "a.py"]],
    [["plugins/x/__init__.py", "__init__.py"], ["plugins/x/../boot.py", "a.py"]],
    [["plugins/x/__init__.py", "/etc/passwd"]],
    [["plugins/x/__init__.py", "__init__.py"], ["plugins/x/package.json", "package.json"]],
])
def test_parse_manifest_rejects_bad_packages(urls):
    with pytest.raises(ValueError):
        _store.parse_manifest("x", {"urls": urls})


def test_parse_manifest_checks_min_firmware():
    urls = [["plugins/x/__init__.py", "__init__.py"]]
    assert _store.parse_manifest("x", {"urls": urls}) == [("__init__.py", "__init__.py")]
    with pytest.raises(ValueError, match="needs GitMatrix"):
        _store.parse_manifest("x", {"urls": urls, "min_firmware": "99.0"})


def test_refresh_caches_index_and_reloads_it(store):
    store.remote.publish("demo", {"__init__.py": GOOD})
    assert store.store.due(store.clock.now)
    assert run(store.store.refresh())
    assert "demo" in store.store.index
    cache = json.loads((store.root / "index-cache.json").read_text())
    assert cache["repo"] == "martinwoodward/gitmatrix-plugins@main"
    assert not store.store.due(store.clock.now)
    assert store.store.due(store.clock.advance(_store.REFRESH_MS))
    # a new Store (next boot) starts from the cache
    assert "demo" in _store.Store().index
    # ...but not one for another repo
    _runtime.firmware = {"PLUGIN_REPO": "someone/else"}
    assert _store.Store().index is None


def test_failed_refresh_keeps_the_cache(store):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    store.remote.files.clear()
    assert not run(store.store.refresh())
    assert "demo" in store.store.index and "404" in store.store.error
    assert store.store.due(store.clock.advance(_store.RETRY_MS))


def test_repo_comes_from_firmware_config_only(store):
    _runtime.firmware = {"PLUGIN_REPO": "a/b", "PLUGIN_BRANCH": "dev"}
    assert _store.base_url() == "https://raw.githubusercontent.com/a/b/dev/"
    _runtime.firmware = {"PLUGIN_REPO": "../../x", "PLUGIN_BRANCH": "../x"}
    assert _store.repo() == ("martinwoodward/gitmatrix-plugins", "main")


# ---- install / update / remove ---------------------------------------------------

def test_install_downloads_commits_and_shows_it(store):
    store.remote.publish("demo", {"__init__.py": GOOD, "assets/pic.txt": "x"}, version="1.2.0")
    run(store.store.refresh())
    p = install(store, "demo")
    assert p["finished"] and p["error"] is None and p["state"] == "done" and p["done"] == 2
    assert (live(store, "demo") / "assets" / "pic.txt").read_text() == "x"
    assert _store.active() == {"demo": {"version": "1.2.0", "hash": store.remote.index["details"]["demo"]["hash"]}}
    assert store.rt.host.current.name == "demo"
    assert "demo" in store.rt.config.get("plugins")
    assert os.listdir(store.root / ".install") == []
    entry = next(p for p in store.store.catalogue()["plugins"] if p["name"] == "demo")
    assert entry["installed"] == "1.2.0" and not entry["update_available"]


def test_hash_mismatch_installs_nothing(store):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    store.remote.files[store.remote.path("demo/__init__.py")] = b"tampered = 1\n"
    p = install(store, "demo")
    assert "hash mismatch" in p["error"] and p["state"] == "failed"
    assert not live(store, "demo").exists() and _store.active() == {}


def test_not_enough_flash(store, monkeypatch):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    monkeypatch.setattr(_store, "free_bytes", lambda: 1000)
    assert "not enough flash" in install(store, "demo")["error"]


def test_big_packages_stage_on_flash(store):
    big = "# " + "x" * (_store.RAM_STAGE_BYTES + 10) + "\n" + GOOD
    store.remote.publish("big", {"__init__.py": big})
    run(store.store.refresh())
    assert install(store, "big")["error"] is None
    assert (live(store, "big") / "__init__.py").read_text() == big


def test_update_keeps_user_files_and_data(store):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    install(store, "demo")
    (live(store, "demo") / "config.json").write_text("{}")         # Art Matrix style
    _state.state("demo")["n"] = 5
    _state.state("demo").save(now=True)
    store.remote.publish("demo", {"__init__.py": GOOD + "V = 2\n"}, version="2.0.0")
    run(store.store.refresh())
    assert store.store.update_available("demo")
    p = install(store, "demo")
    assert p["update"] and p["error"] is None
    assert "V = 2" in (live(store, "demo") / "__init__.py").read_text()
    assert (live(store, "demo") / "config.json").exists()
    assert _state.state("demo")["n"] == 5
    assert _store.active()["demo"]["version"] == "2.0.0"
    assert not store.store.update_available("demo")


def test_broken_update_rolls_back(store):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    install(store, "demo")
    store.remote.publish("demo", {"__init__.py": "def update(:\n"}, version="2.0.0")
    run(store.store.refresh())
    p = install(store, "demo")
    assert "previous version was restored" in p["error"]
    assert (live(store, "demo") / "__init__.py").read_text() == GOOD
    assert _store.active()["demo"]["version"] == "1.0.0"
    assert store.rt.loader.plugins["demo"].module is not None


def test_builtins_cannot_be_installed_over_or_removed(store):
    store.remote.publish("clock", {"__init__.py": GOOD})
    run(store.store.refresh())
    with pytest.raises(_store.StoreError) as e:
        store.store.begin("clock")
    assert e.value.status == 409
    with pytest.raises(_store.StoreError) as e:
        store.store.remove("clock")
    assert e.value.status == 409
    with pytest.raises(_store.StoreError) as e:
        store.store.begin("missing")
    assert e.value.status == 404


@pytest.mark.parametrize("keep", [True, False])
def test_remove_keeps_or_deletes_data(store, keep):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    install(store, "demo")
    _state.state("demo")["n"] = 1
    _state.state("demo").save(now=True)
    (store.root / "secrets").mkdir(exist_ok=True)
    (store.root / "secrets" / "demo.json").write_text('{"token": "x"}')
    assert store.store.remove("demo", keep_data=keep) == {"removed": "demo", "kept_data": keep}
    assert not live(store, "demo").exists() and "demo" not in _store.active()
    assert "demo" not in store.rt.config.get("plugins")
    assert (store.root / "data" / "demo").exists() is keep
    assert (store.root / "secrets" / "demo.json").exists() is keep


# ---- power cuts --------------------------------------------------------------

def _cut_when(monkeypatch, match):
    real = os.rename

    def rename(src, dst):
        if match(str(src), str(dst)):
            raise PowerCut
        return real(src, dst)

    monkeypatch.setattr(os, "rename", rename)
    return real


def test_power_cut_before_the_swap_keeps_the_old_version(store, monkeypatch):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    install(store, "demo")
    store.remote.publish("demo", {"__init__.py": GOOD + "V = 2\n"}, version="2.0.0")
    run(store.store.refresh())
    real = _cut_when(monkeypatch, lambda s, d: d == str(live(store, "demo")))   # stage -> live
    with pytest.raises(PowerCut):
        install(store, "demo")
    assert not live(store, "demo").exists()            # old copy is parked in /.install
    monkeypatch.setattr(os, "rename", real)
    _store.reconcile()
    assert (live(store, "demo") / "__init__.py").read_text() == GOOD
    assert _store.active()["demo"]["version"] == "1.0.0"
    assert os.listdir(store.root / ".install") == []


def test_power_cut_after_the_swap_finishes_the_install(store, monkeypatch):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    install(store, "demo")
    (live(store, "demo") / "mine.txt").write_text("user")
    store.remote.publish("demo", {"__init__.py": GOOD + "V = 2\n"}, version="2.0.0")
    run(store.store.refresh())

    def cut(_data):
        raise PowerCut

    monkeypatch.setattr(_store, "_write_active", cut)
    monkeypatch.setattr(_store, "_carry_over", lambda old, new: None)
    with pytest.raises(PowerCut):
        install(store, "demo")
    monkeypatch.undo()
    assert _store.active()["demo"]["version"] == "1.0.0"   # active.json not yet written
    _store.reconcile()
    assert _store.active()["demo"]["version"] == "2.0.0"
    assert (live(store, "demo") / "mine.txt").read_text() == "user"
    assert not (store.root / ".install" / "journal.json").exists()


def test_power_cut_on_a_fresh_install_leaves_nothing(store, monkeypatch):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    # cut while writing package.json, the last file of the staged copy
    real = _cut_when(monkeypatch, lambda s, d: d.endswith("package.json") and ".install" in d)
    with pytest.raises(PowerCut):
        install(store, "demo")
    monkeypatch.setattr(os, "rename", real)
    _store.reconcile()
    assert not live(store, "demo").exists() and _store.active() == {}
    assert os.listdir(store.root / ".install") == []


def test_reconcile_drops_entries_without_a_complete_folder(store):
    (store.root / "lib" / "plugins" / "active.json").write_text(
        json.dumps({"ghost": {"version": "1", "hash": "x"}, "clock": "bad"}))
    assert _store.reconcile() == {}


# ---- Bluetooth audio: no flash writes while streaming ------------------------------

def test_install_while_streaming_needs_pause_or_queue(store, bt):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    bt.streaming_now = True
    with pytest.raises(_store.StoreError) as e:
        store.store.begin("demo")
    assert e.value.status == 409 and str(e.value) == _flash.AUDIO_WARNING
    p = install(store, "demo", pause_audio=True)
    assert p["error"] is None and p["paused_audio"]
    assert bt.deinits == 1 and bt.inits == 2 and bt.name == "GitMatrix"   # boot + resume


def test_queued_install_waits_for_playback_to_stop(store, bt, monkeypatch):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    monkeypatch.setattr(_store, "QUEUE_POLL_MS", 0)
    bt.streaming_now = True
    store.store.begin("demo", queue=True)

    async def go():
        task = asyncio.create_task(store.store.install("demo"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert store.store.progress["state"] == "queued" and not live(store, "demo").exists()
        bt.streaming_now = False
        await task

    run(go())
    assert store.store.progress["error"] is None and live(store, "demo").exists()
    assert bt.deinits == 0


def test_remove_while_streaming_pauses_only_when_asked(store, bt):
    store.remote.publish("demo", {"__init__.py": GOOD})
    run(store.store.refresh())
    install(store, "demo")
    bt.streaming_now = True
    with pytest.raises(_store.StoreError):
        store.store.remove("demo")
    assert live(store, "demo").exists()
    store.store.remove("demo", pause_audio=True)
    assert not live(store, "demo").exists() and bt.deinits == 1


# ---- the API -------------------------------------------------------------------

SCHEMA = [
    {"key": "feed", "type": "url", "label": "Feed URL", "default": "https://example.com/a.json"},
    {"key": "token", "type": "secret", "label": "Token"},
    {"key": "refresh", "type": "int", "label": "Refresh", "default": 60, "min": 10, "max": 3600},
    {"key": "colour", "type": "color", "label": "Colour", "default": "#40c463"},
    {"key": "mode", "type": "select", "label": "Mode", "options": ["a", "b"], "default": "a"},
]


@pytest.fixture
def web(store, monkeypatch):
    store.net.sta.up = True
    store.rt.wifi.tick(store.clock.advance(500))
    _web.setup(store.rt)
    spawned = []
    monkeypatch.setattr(_web, "spawn", spawned.append)
    store.spawned = spawned
    return store


def call(method, path, body=None):
    from test_web import call as _call

    return _call(method, path, body)


def test_store_api_lists_installs_and_removes(web, bt):
    web.remote.publish("demo", {"__init__.py": GOOD})
    run(web.store.refresh())
    cat = call("GET", "/api/v1/store").json
    assert cat["repo"] == "martinwoodward/gitmatrix-plugins" and cat["online"]
    assert cat["base_url"] == "https://raw.githubusercontent.com/martinwoodward/gitmatrix-plugins/main/"
    assert [p["name"] for p in cat["plugins"]] == ["demo"]
    r = call("POST", "/api/v1/store/install", {"name": "demo"})
    assert r.status == 202 and "warning" not in r.json
    run(web.spawned.pop())
    assert call("GET", "/api/v1/store/install").json["install"]["finished"]
    info = call("GET", "/api/v1/plugins/demo").json
    assert info["installed"] and not info["builtin"] and info["version"] == "1.0.0"
    assert call("POST", "/api/v1/store/install", {"name": "../x"}).status == 400
    assert call("POST", "/api/v1/store/install", {"name": "nope"}).status == 404
    web.remote.publish("clock", {"__init__.py": GOOD})
    run(web.store.refresh())
    assert call("POST", "/api/v1/store/install", {"name": "clock"}).status == 409
    r = call("POST", "/api/v1/plugins/demo/remove", {"keep_data": False})
    assert r.status == 200 and r.json["removed"] == "demo"


def test_store_api_audio_warning(web, bt):
    web.remote.publish("demo", {"__init__.py": GOOD})
    run(web.store.refresh())
    bt.streaming_now = True
    r = call("POST", "/api/v1/store/install", {"name": "demo"})
    assert r.status == 409 and r.json == {"error": _flash.AUDIO_WARNING, "audio_streaming": True}
    r = call("POST", "/api/v1/store/install", {"name": "demo", "queue": True})
    assert r.status == 202 and r.json["warning"] == _flash.AUDIO_WARNING
    web.spawned.pop().close()


def test_store_refresh_api(web):
    web.remote.publish("demo", {"__init__.py": GOOD})
    assert call("POST", "/api/v1/store/refresh").status == 202
    run(web.spawned.pop())
    assert call("POST", "/api/v1/store/refresh").status == 429


def test_settings_api_never_returns_secrets(web, bt):
    make_plugin(web.root, "feed", GOOD, {"name": "Feed", "settings": SCHEMA})
    web.rt.loader.sync_config()
    s = call("GET", "/api/v1/plugins/feed/settings").json
    assert [f["key"] for f in s["schema"]] == ["feed", "token", "refresh", "colour", "mode"]
    assert s["schema"][1] == {"key": "token", "type": "secret", "label": "Token", "set": False}
    assert "token" not in s["values"] and s["values"]["refresh"] == 60
    r = call("POST", "/api/v1/plugins/feed/settings", {"token": "hunter2", "refresh": 5, "colour": "FF0000"})
    assert r.status == 200
    assert r.json["schema"][1]["set"] is True and b"hunter2" not in r.body
    assert r.json["values"]["refresh"] == 10 and r.json["values"]["colour"] == "#ff0000"
    assert json.loads((web.root / "secrets" / "feed.json").read_text()) == {"token": "hunter2"}
    assert "hunter2" not in (web.root / "data" / "feed" / "settings.json").read_text()
    assert _state.settings("feed")["token"] == "hunter2"                 # the plugin sees it
    # other fields can be saved without resending the secret; "" clears it
    call("POST", "/api/v1/plugins/feed/settings", {"mode": "b"})
    assert _state.settings("feed")["token"] == "hunter2"
    call("POST", "/api/v1/plugins/feed/settings", {"token": ""})
    assert _state.settings("feed")["token"] is None
    assert call("POST", "/api/v1/plugins/feed/settings", {"feed": "ftp://x"}).status == 400
    assert call("POST", "/api/v1/plugins/feed/settings", {"mode": "z"}).status == 400


def test_settings_save_calls_on_settings(web, bt):
    make_plugin(web.root, "feed", GOOD + "import builtins\n"
                "def on_settings():\n    builtins.ON_SETTINGS = builtins.__dict__.get('ON_SETTINGS', 0) + 1\n",
                {"name": "Feed", "settings": SCHEMA})
    web.rt.loader.sync_config()
    web.rt.loader.load("feed")
    assert call("POST", "/api/v1/plugins/feed/settings", {"mode": "b"}).status == 200
    import builtins
    assert builtins.ON_SETTINGS == 1
    del builtins.ON_SETTINGS


def test_settings_api_while_streaming(web, bt):
    make_plugin(web.root, "feed", GOOD, {"name": "Feed", "settings": SCHEMA})
    web.rt.loader.sync_config()
    bt.streaming_now = True
    r = call("POST", "/api/v1/plugins/feed/settings", {"mode": "b"})
    assert r.status == 409 and r.json["audio_streaming"]
    assert not (web.root / "data" / "feed" / "settings.json").exists()
    r = call("POST", "/api/v1/plugins/feed/settings", {"mode": "b", "pause_audio": True})
    assert r.status == 200 and r.json["values"]["mode"] == "b"
    assert bt.deinits == 1 and bt.name == "GitMatrix"


def _form(method, path, form):
    from phew.server import Request

    r = Request(method, path.split("?")[0], "HTTP/1.1")
    r.headers = {"host": "gitmatrix.local"}
    r.query = dict(kv.split("=") for kv in path.split("?")[1].split("&"))
    r.form = form
    r.data = form
    return _web.dispatch(r)


def test_config_html_page_and_process_config(web, bt):
    d = make_plugin(web.root, "artish", GOOD + "import builtins\nSAVED = []\n"
                    "def process_config(form):\n    builtins.SAVED_FORM = dict(form)\n", {"name": "Artish"})
    (d / "config.html").write_text('<form method="post"><input name="speed" value="{{ placeholder.SAVED }}">'
                                   "{{ plugin }}</form>")
    web.rt.loader.sync_config()
    r = _form("GET", "/config?plugin=artish", {})
    assert r.status == 200 and b"artish</form>" in r.body and b"[]" in r.body
    assert call("GET", "/api/v1/plugins/artish").json["has_config_html"]
    r = _form("POST", "/config?plugin=artish", {"speed": "3"})
    assert r.status == 303
    import builtins

    assert builtins.__dict__.pop("SAVED_FORM") == {"speed": "3"}
    bt.streaming_now = True
    r = _form("POST", "/config?plugin=artish", {"speed": "4"})
    assert r.status == 409 and b"reconnect your device" in r.body and b'name="pause_audio"' in r.body
    r = _form("POST", "/config?plugin=artish", {"speed": "4", "pause_audio": "1"})
    assert r.status == 303 and bt.deinits == 1


def test_config_html_without_process_config_saves_json(web):
    d = make_plugin(web.root, "plain", GOOD, {"name": "Plain"})
    (d / "config.html").write_text("<form></form>")
    web.rt.loader.sync_config()
    assert _form("POST", "/config?plugin=plain", {"speed": "3"}).status == 303
    assert json.loads((web.root / "data" / "plain" / "config.json").read_text()) == {"speed": "3"}
    assert _form("POST", "/config?plugin=plain", {"reset": "1"}).status == 303
    assert not (web.root / "data" / "plain" / "config.json").exists()
    assert _form("GET", "/config?plugin=nope", {}).status == 404
