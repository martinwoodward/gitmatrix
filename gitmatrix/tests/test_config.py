from gitmatrix._config import Config


def test_first_boot_copies_defaults(root):
    c = Config().load()
    assert (root / "config.py").read_text() == (root / "defaults" / "config.py").read_text()
    assert c.delay == 30 and c.plugins[0] == "graph"


def test_migrate_adds_new_keys_and_keeps_user_values(root):
    (root / "config.py").write_text("# mine\ndelay = 5\nplugins = ['clock']\n")
    c = Config().load()
    assert c.delay == 5 and c.plugins == ["clock"]
    assert c.brightness == 60 and c.dirty
    c.save()
    text = (root / "config.py").read_text()
    assert text.startswith("# mine\ndelay = 5\n")
    assert "brightness = 60" in text
    assert Config().load().delay == 5


def test_broken_config_is_set_aside(root):
    (root / "config.py").write_text("delay = = 3\n")
    c = Config().load()
    assert c.repaired and c.delay == 30
    assert (root / "config.py.bad").read_text() == "delay = = 3\n"


def test_save_rewrites_in_place_keeping_comments_and_multiline(root):
    (root / "config.py").write_text("# top\nplugins = [\n    'a',\n    'b',\n]\n# delay\ndelay = 30\nold_key = 1\n")
    c = Config().load()
    c.set("plugins", ["b", "a"])
    c.set("delay", 10)
    del c.values["old_key"]
    c.save()
    text = (root / "config.py").read_text()
    assert "# top\nplugins = ['b', 'a']\n# delay\ndelay = 10\n" in text
    assert "old_key" not in text and "'a',\n" not in text
    assert Config().load().plugins == ["b", "a"]


def test_set_only_dirty_on_change(root):
    c = Config().load()
    c.save(force=True)
    c.set("delay", 30)
    assert not c.dirty
    c.set("delay", 31)
    assert c.dirty


def test_non_simple_values_are_ignored(root):
    (root / "config.py").write_text("import os\nx = os\ndelay = 7\n")
    c = Config().load()
    assert c.delay == 7 and c.get("x") is None and c.get("os") is None
