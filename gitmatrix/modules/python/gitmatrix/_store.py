# The plugin store: the catalogue (index.json) and transactional install, update
# and remove. Modelled on Art Matrix's install flow so its plugins port as-is.
#
# Repository: PLUGIN_REPO / PLUGIN_BRANCH in /gitmatrix_config.py (not in the
# web UI). The index comes from https://raw.githubusercontent.com/<repo>/<branch>/
# index.json over fetch.AsyncFetch (TLS with CERT_NONE, as Art Matrix), is cached
# in /index-cache.json and refreshed every 15 minutes while WiFi is up.
#
# Install / update, one at a time:
#   1. read <plugin>/package.json, validate it and check there's room on flash
#   2. download every file: into RAM when small, else straight to the staging
#      folder /.install/<name>@<hash>/; check the package hash
#   3. write the staged copy in one burst, package.json last (= complete).
#      Never while Bluetooth audio streams (a flash write then can hang the
#      board): the request either says pause_audio (the sink stops for the
#      burst; the phone must reconnect) or queue (wait until playback stops)
#   4. commit without yielding: journal, rename the live /lib/plugins/<name> to
#      /.install/<name>.old, rename the staged copy into place, carry over files
#      the plugin made itself, atomically rewrite /lib/plugins/active.json
#   5. import it; if an update fails to import, roll back to the old copy
# reconcile() runs at boot before anything is loaded and finishes or undoes a
# commit cut off by a power cut, then sweeps /.install.
#
# Plugins keep the plain /lib/plugins/<name>/ path Art Matrix plugins expect
# (several open files there by absolute path). User data goes in /data/<name>/
# and secrets in /secrets/<name>.json, which updates never touch.

import json
import os

from gitmatrix import _compat, _flash, _log, _runtime

INDEX_HOST = "raw.githubusercontent.com"
DEFAULT_REPO = "martinwoodward/gitmatrix-plugins"
DEFAULT_BRANCH = "main"
CACHE = "/index-cache.json"
INSTALL_DIR = "/.install"
JOURNAL = INSTALL_DIR + "/journal.json"
ACTIVE = "/lib/plugins/active.json"
MANIFEST = "package.json"

REFRESH_MS = 15 * 60 * 1000       # index refresh while online
RETRY_MS = 60 * 1000              # after a failed refresh
QUICK_RETRY_MS = 10 * 1000        # after the first QUICK_RETRIES failures in a row: the
QUICK_RETRIES = 2                 # first fetch just after joining can time out (ETIMEDOUT)
MANUAL_REFRESH_MS = 10 * 1000     # rate limit for the Refresh button
TIMEOUT_S = 10                    # no data for this long fails a download
TOTAL_TIMEOUT_MS = 60 * 1000      # one file, start to finish
MAX_INDEX_BYTES = 64 * 1024
MAX_FILE_BYTES = 256 * 1024
RAM_STAGE_BYTES = 48 * 1024       # bigger plugins download straight to flash
SPACE_MARGIN = 32 * 1024          # left free after an install
HASH_LENGTH = 12
QUEUE_POLL_MS = 1000              # how often a queued install checks playback
AUDIO_WARNING = _flash.AUDIO_WARNING


class StoreError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---- small helpers ------------------------------------------------------------

async def _sleep_ms(ms):
    import asyncio

    if hasattr(asyncio, "sleep_ms"):
        await asyncio.sleep_ms(ms)
    else:
        await asyncio.sleep(ms / 1000)


def _read_json(path, default):
    try:
        with open(path) as f:
            value = json.load(f)
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    folder = path[:path.rfind("/")]
    if folder:
        _compat.makedirs(folder)
    _compat.write_atomic(path, json.dumps(value))


def _valid_repo(text):
    if not isinstance(text, str) or text.count("/") != 1:
        return False
    for part in text.split("/"):
        if not part or part.startswith("."):
            return False
        for ch in part:
            if not (ch.isalpha() or ch.isdigit() or ch in "-_."):
                return False
    return True


def _valid_branch(text):
    if not isinstance(text, str) or not text or ".." in text or text.startswith("/"):
        return False
    for ch in text:
        if not (ch.isalpha() or ch.isdigit() or ch in "-_./"):
            return False
    return True


def repo():
    """(owner/name, branch) from /gitmatrix_config.py, defaults if unusable."""
    fw = _runtime.firmware or {}
    name = fw.get("PLUGIN_REPO") or DEFAULT_REPO
    branch = fw.get("PLUGIN_BRANCH") or DEFAULT_BRANCH
    if not _valid_repo(name):
        name = DEFAULT_REPO
    if not _valid_branch(branch):
        branch = DEFAULT_BRANCH
    return name, branch


def base_path():
    name, branch = repo()
    return f"/{name}/{branch}"


def base_url():
    return f"https://{INDEX_HOST}{base_path()}/"


def version_tuple(text):
    out = []
    for part in str(text or "0").split(".")[:3]:
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def compatible(min_firmware):
    if not min_firmware:
        return True
    import gitmatrix

    return version_tuple(gitmatrix.VERSION) >= version_tuple(min_firmware)


def free_bytes():
    try:
        st = os.statvfs(_runtime.path("/") or "/")
    except (OSError, AttributeError):
        return None
    return st[1] * st[3]


def _safe_rel(path):
    """A relative file path inside a plugin folder."""
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    return all(part and part not in (".", "..") for part in path.split("/"))


def _sha1():
    try:
        import hashlib

        return hashlib.sha1()
    except (ImportError, AttributeError):
        return None


def package_hash(files):
    """The generator's hash: sha1 over sorted (path, bytes); None without sha1.
    files: [(path, bytes or a filename to read)]."""
    digest = _sha1()
    if digest is None:
        return None
    for path, data in sorted(files, key=lambda f: f[0]):
        digest.update(path.encode() + b"\0")
        if isinstance(data, str):
            with open(data, "rb") as f:
                while True:
                    chunk = f.read(1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        else:
            digest.update(data)
    import binascii

    # MicroPython's hashlib has digest() but no hexdigest()
    return binascii.hexlify(digest.digest()).decode()[:HASH_LENGTH]


# ---- HTTP -----------------------------------------------------------------------

async def http_get(path, max_bytes=MAX_FILE_BYTES, file=None):
    """GET https://raw.githubusercontent.com<path>; bytes, or None when saved to
    file. Raises on an HTTP error, a timeout or a too-big response. Tests replace it."""
    import fetch

    f = fetch.AsyncFetch(INDEX_HOST, timeout=TIMEOUT_S, max_bytes=max_bytes)
    deadline = _compat.ticks_add(_compat.ticks_ms(), TOTAL_TIMEOUT_MS)
    try:
        f.fetch(path, file=file)
        while f.update() == fetch.AsyncFetch.FETCHING:
            if _compat.ticks_diff(_compat.ticks_ms(), deadline) > 0:
                raise OSError("timed out")
            await _sleep_ms(5)
        if f.status != fetch.AsyncFetch.DONE:
            raise OSError(f"HTTP {f.http_status}")
        return None if file else bytes(f.body)
    finally:
        f.reset()


# ---- index ------------------------------------------------------------------------

def _str(value, limit=200):
    return value[:limit] if isinstance(value, str) else ""


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_index(data):
    """Art Matrix's index ({"plugins": {name: title}, "details": {name: {...}}})
    plus GitMatrix's extra details, normalised to {name: entry}."""
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict):
        raise ValueError("not a plugin index")
    details = data.get("details")
    details = details if isinstance(details, dict) else {}
    out = {}
    for name, title in data["plugins"].items():
        if not _compat.valid_name(name) or not isinstance(title, str) or not title.strip():
            continue
        d = details.get(name)
        d = d if isinstance(d, dict) else {}
        tags = d.get("tags")
        out[name] = {
            "name": name,
            "title": title[:60],
            "description": _str(d.get("description")),
            "author": _str(d.get("author"), 60),
            "category": _str(d.get("category"), 30),
            "hash": _str(d.get("hash"), 40),
            "version": _str(d.get("version"), 20),
            "tags": [t[:30] for t in tags if isinstance(t, str)][:10] if isinstance(tags, list) else [],
            "size": _int(d.get("size")),
            "files": _int(d.get("files")),
            "requires_internet": bool(d.get("requires_internet", False)),
            "min_firmware": _str(d.get("min_firmware"), 20),
            "settings": bool(d.get("settings")),
            "config": bool(d.get("config")),
            "input": bool(d.get("input")),
        }
    return out


def parse_manifest(name, data):
    """Check a downloaded package.json; returns [(dest_rel, src_rel)]."""
    if not isinstance(data, dict):
        raise ValueError("package.json is not an object")
    urls = data.get("urls")
    if not isinstance(urls, list) or not urls:
        raise ValueError("package.json lists no files")
    prefix = f"plugins/{name}/"
    files = []
    for item in urls:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("bad urls entry")
        dest, src = item
        if not isinstance(dest, str) or not dest.startswith(prefix) or ".." in dest:
            raise ValueError(f"bad file {dest!r}")
        rel = dest[len(prefix):]
        if not _safe_rel(rel) or not _safe_rel(src) or rel == MANIFEST:
            raise ValueError(f"bad file {dest!r}")
        files.append((rel, src))
    if "__init__.py" not in [rel for rel, _ in files]:
        raise ValueError("package has no __init__.py")
    if not compatible(data.get("min_firmware")):
        raise ValueError(f"needs GitMatrix {data.get('min_firmware')} or later")
    return files


# ---- active.json ---------------------------------------------------------------------

def active():
    """{name: {"version", "hash"}} for every plugin installed from the store."""
    data = _read_json(_runtime.path(ACTIVE), {})
    return {k: v for k, v in data.items() if isinstance(v, dict) and _compat.valid_name(k)}


generation = 0  # bumped whenever the installed set changes; lets callers cache per-plugin info


def _write_active(data):
    global generation
    _write_json(_runtime.path(ACTIVE), data)
    generation += 1


def live_dir(name):
    return _runtime.plugins_dir() + "/" + name


def _complete(folder, hash_=None):
    """__init__.py and package.json both there (package.json is written last, so
    marks a whole copy); with hash_, package.json must carry that hash."""
    if not _compat.exists(folder + "/__init__.py") or not _compat.exists(folder + "/" + MANIFEST):
        return False
    if hash_:
        return _read_json(folder + "/" + MANIFEST, {}).get("hash", hash_) == hash_
    return True


def is_builtin(name):
    return name not in active() and _compat.exists(live_dir(name) + "/__init__.py")


def _shipped(folder):
    m = _read_json(folder + "/" + MANIFEST, {})
    out = {MANIFEST}
    for item in m.get("urls", []) if isinstance(m.get("urls"), list) else []:
        if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], str):
            out.add(item[1])
    return out


def _carry_over(old, new):
    """Move files the old copy made itself (neither version ships them) into the
    new one, as Art Matrix does: config.json, a drawn picture, a cache."""
    shipped = _shipped(old) | _shipped(new)
    folders = [""]
    while folders:
        folder = folders.pop()
        try:
            entries = os.listdir(old + folder)
        except OSError:
            continue
        for entry in entries:
            rel = (folder + "/" + entry)[1:]
            src = old + "/" + rel
            if _compat.is_dir(src):
                if not _compat.exists(new + "/" + rel):
                    os.mkdir(new + "/" + rel)
                folders.append("/" + rel)
            elif rel not in shipped and not _compat.exists(new + "/" + rel):
                os.rename(src, new + "/" + rel)


def reconcile():
    """At boot, before plugins load: finish or undo a cut-off commit, drop
    active.json entries with no complete folder, empty /.install."""
    work = _runtime.path(INSTALL_DIR)
    journal = _read_json(_runtime.path(JOURNAL), {})
    data = active()
    changed = False
    if _compat.valid_name(journal.get("name", "")):
        name = journal["name"]
        live = live_dir(name)
        old = work + "/" + name + ".old"
        if _complete(live, journal.get("hash")):
            # the new copy made it into place: finish the job
            if _compat.is_dir(old):
                _carry_over(old, live)
            data[name] = {"version": journal.get("version", ""), "hash": journal.get("hash", "")}
            _log.log(f"store: finished installing {name}")
        elif _compat.is_dir(old):
            # back to the last known good copy
            _compat.remove_tree(live)
            os.rename(old, live)
            prev = journal.get("previous")
            if isinstance(prev, dict):
                data[name] = prev
            else:
                data.pop(name, None)
            _log.log(f"store: install of {name} cut off, restored the previous version")
        else:
            _compat.remove_tree(live)
            data.pop(name, None)
            _log.log(f"store: install of {name} cut off, removed it")
        changed = True
    for name in list(data):
        if not _complete(live_dir(name)):
            data.pop(name)
            changed = True
    if changed:
        _write_active(data)
    if _compat.is_dir(work):
        for entry in os.listdir(work):
            _compat.remove_tree(work + "/" + entry)
    return data


# ---- the store -----------------------------------------------------------------------

class Store:
    def __init__(self):
        self.index = None          # {name: entry}, None until loaded or fetched
        self.index_repo = None
        self.checked_at = None     # ticks of the last refresh attempt
        self.fetched_at = None     # ticks of the last good fetch
        self.error = None
        self.failures = 0          # failed refreshes in a row
        self.refreshing = False
        self.progress = None       # install/update progress for the UI
        self.pending_cache = None  # index to write once Bluetooth audio stops
        self.feed = lambda: None   # the watchdog, during long flash writes
        _runtime.store = self
        self.load_cache()

    # -- index --

    def load_cache(self):
        cached = _read_json(_runtime.path(CACHE), {})
        name, branch = repo()
        if cached.get("repo") != f"{name}@{branch}":
            return False
        try:
            self.index = parse_index(cached.get("index"))
        except ValueError:
            return False
        self.index_repo = cached["repo"]
        return True

    def due(self, now):
        if self.refreshing or self.busy():
            return False
        if self.checked_at is None:
            return True
        if not self.error:
            wait = REFRESH_MS
        else:
            wait = QUICK_RETRY_MS if self.failures <= QUICK_RETRIES else RETRY_MS
        return _compat.ticks_diff(now, self.checked_at) >= wait

    def refresh_wait_ms(self, now=None):
        if self.checked_at is None:
            return 0
        now = _compat.ticks_ms() if now is None else now
        return max(0, MANUAL_REFRESH_MS - _compat.ticks_diff(now, self.checked_at))

    async def refresh(self):
        """Fetch index.json; the cache is rewritten only when it changed."""
        self.refreshing = True
        name, branch = repo()
        try:
            raw = await http_get(base_path() + "/index.json", MAX_INDEX_BYTES)
            data = json.loads(raw)
            index = parse_index(data)
            key = f"{name}@{branch}"
            if index != self.index or key != self.index_repo:
                self.pending_cache = {"repo": key, "index": data}
                self.flush()
            self.index = index
            self.index_repo = key
            self.error = None
            self.failures = 0
            self.fetched_at = _compat.ticks_ms()
            return True
        except Exception as e:  # noqa: BLE001 - offline, DNS, HTTP, bad JSON: keep the cache
            self.error = _compat.error_text(e)
            self.failures += 1
            _log.log(f"store: index refresh failed ({self.error})")
            return False
        finally:
            self.checked_at = _compat.ticks_ms()
            self.refreshing = False

    def flush(self):
        """Write a fetched index to the cache, unless audio is streaming
        (housekeeping calls this again once it stops)."""
        if self.pending_cache is None or not _flash.can_write():
            return False
        try:
            _write_json(_runtime.path(CACHE), self.pending_cache)
        except OSError as e:
            _log.log(f"store: could not cache the index ({_compat.error_text(e)})")
            return False
        self.pending_cache = None
        return True

    def entry(self, name):
        return (self.index or {}).get(name)

    def update_available(self, name, installed=None):
        installed = active() if installed is None else installed
        a = installed.get(name)
        e = self.entry(name)
        return bool(a and e and e["hash"] and a.get("hash") != e["hash"] and compatible(e["min_firmware"]))

    def catalogue(self):
        installed = active()
        name, branch = repo()
        plugins = []
        for key in sorted(self.index or {}):
            item = dict(self.index[key])
            a = installed.get(key)
            item["installed"] = a.get("version") or True if a else None
            item["builtin"] = a is None and _compat.exists(live_dir(key) + "/__init__.py")
            item["update_available"] = self.update_available(key, installed)
            item["compatible"] = compatible(item["min_firmware"])
            plugins.append(item)
        now = _compat.ticks_ms()
        wifi = _runtime.wifi
        return {
            "repo": name,
            "branch": branch,
            "base_url": base_url(),
            "plugins": plugins,
            "loaded": self.index is not None,
            "age_ms": _compat.ticks_diff(now, self.fetched_at) if self.fetched_at is not None else None,
            "refreshing": self.refreshing,
            "error": self.error,
            "online": bool(wifi is not None and wifi.connected()),
            "install": self.progress,
            "free": free_bytes(),
        }

    # -- install / update --

    def busy(self):
        return self.progress is not None and not self.progress["finished"]

    def begin(self, name, pause_audio=False, queue=False):
        """Validate an install request and set up its progress; raises StoreError.
        While Bluetooth audio streams it needs pause_audio or queue (409 without)."""
        if _flash.streaming() and not (pause_audio or queue):
            raise StoreError(409, AUDIO_WARNING)
        if self.busy():
            raise StoreError(409, "another install is running")
        entry = self.entry(name)
        if entry is None:
            raise StoreError(404, f"'{name}' is not in the plugin index")
        if is_builtin(name):
            raise StoreError(409, f"'{name}' is built in")
        if not compatible(entry["min_firmware"]):
            raise StoreError(409, f"'{name}' needs GitMatrix {entry['min_firmware']} or later")
        self.progress = {
            "plugin": name,
            "name": entry["title"],
            "update": name in active(),
            "done": 0,
            "total": entry["files"] or 0,
            "finished": False,
            "error": None,
            "state": "downloading",
            "pause_audio": bool(pause_audio),
            "queue": bool(queue),
            "paused_audio": False,
        }
        return self.progress

    async def _writable(self, progress):
        """Wait (queue) until flash may be written; the caller then writes inside
        pause(), which stops the sink if pause_audio was given."""
        while _flash.streaming():
            if progress["pause_audio"]:
                return
            if not progress["queue"]:
                raise StoreError(409, "Bluetooth audio started; " + AUDIO_WARNING)
            progress["state"] = "queued"
            await _sleep_ms(QUEUE_POLL_MS)
        progress["state"] = "installing"

    async def install(self, name):
        """Run an install begun with begin(); never raises."""
        progress = self.progress
        work = _runtime.path(INSTALL_DIR)
        stage = None
        held = None
        try:
            entry = self.entry(name)
            raw = await http_get(f"{base_path()}/{name}/{MANIFEST}", MAX_INDEX_BYTES)
            manifest = json.loads(raw)
            files = parse_manifest(name, manifest)
            progress["total"] = len(files)
            size = entry["size"] if entry else None
            free = free_bytes()
            need = (size or 0) + SPACE_MARGIN
            if free is not None and free < need:
                raise StoreError(507, f"not enough flash: needs {need // 1024} KB, {free // 1024} KB free")

            hash_ = str(manifest.get("hash") or (entry or {}).get("hash") or "new")[:HASH_LENGTH]
            in_ram = size is not None and size <= RAM_STAGE_BYTES
            if not in_ram:
                # too big for RAM: it downloads straight to flash, so the sink
                # (if paused) stays down for the whole download
                await self._writable(progress)
                held = _flash.pause()
                held.__enter__()
                progress["paused_audio"] = held.stopped
                progress["state"] = "downloading"
            stage = f"{work}/{name}@{hash_}"
            got = []                          # (rel, bytes or staged filename)
            for rel, src in files:
                url = f"{base_path()}/{name}/{src}"
                if in_ram:
                    got.append((rel, await http_get(url)))
                else:
                    if not got:
                        _compat.remove_tree(stage)
                        _compat.makedirs(stage)
                    target = stage + "/" + rel
                    _ensure_parent(target)
                    await http_get(url, file=target)
                    got.append((rel, target))
                progress["done"] += 1

            want = manifest.get("hash")
            have = package_hash(got)
            if want and have is not None and have != want:
                raise StoreError(502, "download corrupted (hash mismatch)")

            if held is None:
                await self._writable(progress)
            version = manifest.get("version") if isinstance(manifest.get("version"), str) else ""
            # one burst of flash writes with no awaits, package.json last marks
            # the copy complete; then the commit
            with _flash.pause() as burst:
                if burst.stopped:
                    progress["paused_audio"] = True
                if in_ram:
                    _compat.remove_tree(stage)
                    _compat.makedirs(stage)
                for rel, data in got:
                    if not isinstance(data, str):
                        target = stage + "/" + rel
                        _ensure_parent(target)
                        _flash.check()
                        with open(target, "wb") as f:
                            f.write(data)
                        self.feed()
                got = None
                _compat.write_atomic(stage + "/" + MANIFEST, json.dumps(manifest))
                message = self._commit(name, stage, version, hash_)
                stage = None
            if message:
                progress["error"] = message
        except StoreError as e:
            progress["error"] = str(e)
        except _flash.FlashBusy:
            progress["error"] = "Bluetooth audio started during the install; try again with the audio paused"
        except Exception as e:  # noqa: BLE001 - network, flash or a bad package: report it
            progress["error"] = f"install failed: {_compat.error_text(e)}"
        finally:
            if stage is not None:
                try:
                    with _flash.pause():
                        _compat.remove_tree(stage)
                except OSError:
                    pass
            if held is not None:
                held.__exit__(None, None, None)
            progress["state"] = "failed" if progress["error"] else "done"
            if progress["error"]:
                _log.log(f"store: {name}: {progress['error']}")
            progress["finished"] = True
            _compat.collect()

    def _commit(self, name, stage, version, hash_):
        """Swap the staged copy in (no awaits), then load it. Returns an error
        message, or None. Runs inside _flash.pause()."""
        _flash.check()
        work = _runtime.path(INSTALL_DIR)
        live = live_dir(name)
        old = work + "/" + name + ".old"
        data = active()
        previous = data.get(name)
        was_update = _compat.exists(live + "/__init__.py")
        showing = _stop(name)

        _compat.remove_tree(old)
        _write_json(_runtime.path(JOURNAL), {"name": name, "version": version, "hash": hash_,
                                            "previous": previous})
        if was_update:
            os.rename(live, old)
        else:
            _compat.remove_tree(live)
        _compat.makedirs(_runtime.plugins_dir())
        os.rename(stage, live)
        if was_update:
            _carry_over(old, live)
        data[name] = {"version": version, "hash": hash_}
        _write_active(data)
        os.remove(_runtime.path(JOURNAL))

        from gitmatrix import _state

        _state.forget(name)
        error = None
        if not _start(name, showing, fresh=not was_update) and was_update:
            # the new version won't import: put the old one back
            _stop(name)
            _compat.remove_tree(live)
            os.rename(old, live)
            if previous is not None:
                data[name] = previous
            else:
                data.pop(name, None)
            _write_active(data)
            loader = _runtime.loader
            if loader is not None:
                loader.unquarantine(name)
            _start(name, showing, fresh=False)
            error = "the update failed to start, so the previous version was restored"
        elif _runtime.loader is not None and name in _runtime.loader.config.get("quarantined", {}):
            error = "installed, but it failed to start"
        _compat.remove_tree(old)
        return error

    # -- remove --

    def remove(self, name, keep_data=True, pause_audio=False):
        if _flash.streaming() and not pause_audio:
            raise StoreError(409, AUDIO_WARNING)
        if self.busy():
            raise StoreError(409, "an install is running")
        data = active()
        if name not in data:
            if is_builtin(name):
                raise StoreError(409, f"'{name}' is built in and can't be removed")
            raise StoreError(404, f"no installed plugin '{name}'")
        _stop(name)
        live = live_dir(name)
        work = _runtime.path(INSTALL_DIR)
        gone = work + "/" + name + ".removed"
        from gitmatrix import _state

        with _flash.pause():
            _compat.makedirs(work)
            _compat.remove_tree(gone)
            if _compat.exists(live):
                os.rename(live, gone)
            data.pop(name)
            _write_active(data)
            _compat.remove_tree(gone)
            _state.forget(name, delete=not keep_data)
        loader = _runtime.loader
        if loader is not None:
            loader.sync_config()
            host = _runtime.host
            if host is not None and host.current is None and host.plugins():
                host.step(1, _compat.ticks_ms())
        return {"removed": name, "kept_data": bool(keep_data)}


def _ensure_parent(path):
    folder = path[:path.rfind("/")]
    if folder and not _compat.exists(folder):
        _compat.makedirs(folder)


def _stop(name):
    """Unload a plugin before its files change; True if it was on screen."""
    host = _runtime.host
    loader = _runtime.loader
    showing = host is not None and host.current is not None and host.current.name == name
    if showing:
        host._enter(None, _compat.ticks_ms())  # noqa: SLF001 - calls on_hide first
        host.prev = None
    if loader is not None:
        loader.unload(name)
    return showing


def _start(name, showing, fresh):
    """Load a just-installed plugin. A fresh install is switched on and shown;
    an update keeps its enabled state. False if it failed to import."""
    loader = _runtime.loader
    host = _runtime.host
    if loader is None:
        return True
    loader.sync_config()
    cfg = loader.config
    if fresh:
        cfg.set("disabled_plugins", [p for p in cfg.get("disabled_plugins", []) if p != name])
    if name in cfg.get("disabled_plugins", []) or (host is not None and host.safe_mode):
        return True
    plugin = loader.load(name)
    if plugin is None:
        return False
    if host is not None and (showing or fresh):
        host.show(name)
    elif host is not None and host.current is None:
        host.step(1, _compat.ticks_ms())
    return True
