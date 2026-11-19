"""tools/preview.py: ROM font rendering matches the device, and a plugin records to a GIF."""

import os
import subprocess
import sys
import types

import pytest
import stubs

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import preview  # noqa: E402


def _rows(img):
    return ["".join("#" if img.pixel(x, y) & 0xFF else "." for x in range(20)) for y in range(img.height)]


def test_sins_text_matches_the_device():
    # captured from a 1.29 widget: screen.text("Ag! A1", vec2(0, -3)) with font.sins
    sins = preview.PixelFont(os.path.join(preview.FONTS, "sins.ppf"))
    assert (sins.measure("Ag!"), sins.measure("A A"), sins.measure(" "), sins.measure("")) == (16, 15, 3, 0)
    img = stubs.image()
    sins.draw(img, "Ag! A1", 0, -3, 0xFFFFFFFF)
    assert _rows(img) == [
        "..###.........#.....",
        ".#..#.........#.....",
        ".#..#...##....#.....",
        ".####..#..#...#.....",
        ".#..#..#..#...#.....",
        ".#..#...###.........",
        ".#..#.....#...#.....",
    ]
    img = stubs.image()
    sins.draw(img, "A", -1.2, -3, 0xFFFFFFFF)  # truncates toward zero, like the device
    assert _rows(img)[0] == ".###................"


def test_preview_records_the_template(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    out = tmp_path / "preview.gif"
    template = os.path.join(ROOT, "templates", "plugin", "my_plugin")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "preview.py"), template, "-o", str(out)],
                   check=True, capture_output=True)
    with pil.open(out) as gif:
        assert gif.size == (53 * 8, 7 * 8)
        assert gif.n_frames > 10
        assert gif.info["loop"] == 0


def test_preview_spec_reads_fixtures(tmp_path):
    (tmp_path / "preview").mkdir()
    (tmp_path / "preview" / "data.json").write_text('{"v": 1}')
    (tmp_path / "preview" / "preview.json").write_text(
        '{"seconds": 3, "settings": {"url": "x"}, "fetch": {"example.com/api/v1?q=1": "data.json"}}')
    spec = preview.read_spec(tmp_path)
    assert spec["seconds"] == 3 and spec["settings"] == {"url": "x"}
    assert spec["responses"] == {"example.com/api/v1": b'{"v": 1}'}
    (tmp_path / "preview" / "preview.json").write_text('{"fetch": {"x": "../../etc/passwd"}}')
    with pytest.raises(ValueError):
        preview.read_spec(tmp_path)
    (tmp_path / "preview" / "preview.json").write_text('{"seconds": 60}')
    with pytest.raises(ValueError):
        preview.read_spec(tmp_path)


def test_preview_fetch_serves_fixtures():
    fake = preview.preview_fetch({"example.com/api": b'{"v": 2}'})
    import fetch

    got = types.SimpleNamespace(ok=None, status=None)
    f = fake("example.com")
    f.on_complete(lambda r: setattr(got, "ok", r.to_json()))
    f.fetch("/api?x=1")
    while f.update() == fetch.AsyncFetch.FETCHING:
        pass
    assert got.ok == {"v": 2}

    missing = fake("example.com")
    missing.on_error(lambda r: setattr(got, "status", r.http_status) or True)
    missing.fetch("/nope")
    while missing.update() == fetch.AsyncFetch.FETCHING:
        pass
    assert got.status == 404
