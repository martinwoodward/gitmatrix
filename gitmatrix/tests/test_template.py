"""The plugin template in templates/plugin/ loads and runs under the real runtime."""

import json
import os
import shutil

from conftest import set_plugins

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "templates", "plugin", "my_plugin")


def test_template_plugin_loads_runs_and_takes_input(rig):
    shutil.copytree(TEMPLATE, rig.root / "lib" / "plugins" / "my_plugin")
    set_plugins(rig.root, ["my_plugin"])
    rt = rig.boot()
    rig.frames(240)

    plugin = rt.loader.plugins["my_plugin"]
    assert rt.host.current.name == "my_plugin"
    assert "my_plugin" not in rt.config.quarantined
    assert plugin.error_count == 0
    assert plugin.title == "My Plugin"
    assert plugin.input

    import gitmatrix

    assert gitmatrix.settings("my_plugin")["speed"] == 20
    assert gitmatrix.state("my_plugin").get("bounces", 0) >= 1  # 20 px/s for ~4 s crosses 53 px
    rig.turn(3)
    assert gitmatrix.state("my_plugin")["boost"] == 3
    assert rt.host.current.name == "my_plugin"  # input plugins keep the knob
    assert plugin.error_count == 0


def test_template_manifest_passes_store_rules():
    with open(os.path.join(TEMPLATE, "package.json")) as f:
        manifest = json.load(f)
    for key in ("name", "version", "description", "author", "category", "requires_internet"):
        assert key in manifest
    assert len(manifest["description"]) <= 60
    assert "hash" not in manifest and "urls" not in manifest
    with open(os.path.join(TEMPLATE, "thumbnail.png"), "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    assert (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")) == (53, 7)
