# Code CPython accepts but MicroPython 1.29 rejects (at import time, or at runtime).
import ast
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = sorted(p for d in ("modules/python", "firmware", "templates") for p in (ROOT / d).rglob("*.py"))

# CPython str/int methods that MicroPython 1.29 doesn't implement (AttributeError at
# runtime). Checked by name, so a same-named method on our own class would also trip.
MISSING_METHODS = {
    "capitalize", "title", "ljust", "rjust", "zfill", "casefold", "removeprefix", "removesuffix",
    "swapcase", "expandtabs", "translate", "maketrans", "isalnum", "isnumeric", "isdecimal",
    "isidentifier", "isprintable", "istitle", "isascii", "format_map", "bit_length",
}
# mat3().translate(x, y) is picovector's, not str.translate
ALLOWED_RECEIVERS = {"translate": "mat3"}


def unsupported(tree):
    for node in ast.walk(tree):
        # [*a, b], (*a, b), {*a}: "*x must be assignment target" / "invalid syntax"
        # (a, *b = x is fine: that's an assignment target, ctx Store)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not isinstance(getattr(node, "ctx", None), ast.Store):
            if any(isinstance(e, ast.Starred) for e in node.elts):
                yield node.lineno, "starred item in a display"
        # {**a, ...}
        if isinstance(node, ast.Dict) and None in node.keys:
            yield node.lineno, "** in a dict display"
        # contribution_widget's globals are in the builtins override dict: reading
        # builtins.screen raises AttributeError on the device (writing is fine)
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name) and node.value.id == "builtins"):
            yield node.lineno, f"reads builtins.{node.attr}"
        # the emitters are chosen at compile time from the literal @micropython.viper /
        # .native / .asm_thumb; an alias like `viper = micropython.viper` runs as plain
        # Python (ptr16 etc. are then NameErrors)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id in ("viper", "native", "asm_thumb"):
                    yield dec.lineno, f"@{dec.id} must be written @micropython.{dec.id}"
        if isinstance(node, ast.Attribute) and node.attr == "hexdigest":
            yield node.lineno, "hashlib hexdigest() (MicroPython only has digest())"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MISSING_METHODS:
            recv = node.func.value
            allowed = ALLOWED_RECEIVERS.get(node.func.attr)
            if not (allowed and isinstance(recv, ast.Call) and getattr(recv.func, "id", None) == allowed):
                yield node.lineno, f".{node.func.attr}() doesn't exist on MicroPython"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_source_compiles_on_micropython(path):
    found = list(unsupported(ast.parse(path.read_text(), str(path))))
    assert not found, f"{path.name}: {found}"


def _mpy_cross():
    try:
        import mpy_cross  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "mpy_cross"]


@pytest.mark.skipif(_mpy_cross() is None, reason="pip install 'mpy-cross==1.29.*' to compile with the real 1.29 parser")
@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_mpy_cross_1_29_compiles(path, tmp_path):
    # -march for @micropython.viper/native code (the RP2350 port uses armv7emsp)
    out = subprocess.run([*_mpy_cross(), "-march=armv7emsp", "-o", str(tmp_path / "out.mpy"), str(path)],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
