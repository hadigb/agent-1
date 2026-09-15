"""Minimal stand-in for pytest (used only when pytest is not installed).

Run:  python tests/run_tests.py
It supports what this suite needs: test discovery, ``pytest.fixture`` /
``pytest.raises``, and the ``tmp_path``, ``capsys``, ``monkeypatch`` fixtures.
"""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import sys
import tempfile
import traceback
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:  # real pytest available -> just delegate
    import pytest  # type: ignore

    if __name__ == "__main__":
        sys.exit(pytest.main([str(HERE), "-q"]))
except ImportError:
    pytest = None  # type: ignore


class _Raises:
    def __init__(self, exc):
        self.exc = exc
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError(f"DID NOT RAISE {self.exc}")
        if issubclass(et, self.exc):
            self.value = ev
            return True
        return False


def _fixture(fn=None, **kw):
    def deco(f):
        f._is_fixture = True
        return f
    return deco(fn) if fn else deco


fake = types.ModuleType("pytest")
fake.fixture = _fixture
fake.raises = _Raises
fake.skip = lambda *a, **k: (_ for _ in ()).throw(_Skip())
sys.modules["pytest"] = fake


class _Skip(Exception):
    pass


class _Capsys:
    def __init__(self):
        self.out = io.StringIO()
        self.err = io.StringIO()
        self._ctx = None

    def start(self):
        self._o = contextlib.redirect_stdout(self.out)
        self._e = contextlib.redirect_stderr(self.err)
        self._o.__enter__()
        self._e.__enter__()

    def stop(self):
        self._e.__exit__(None, None, None)
        self._o.__exit__(None, None, None)

    def readouterr(self):
        o, e = self.out.getvalue(), self.err.getvalue()
        self.out.seek(0); self.out.truncate(); self.err.seek(0); self.err.truncate()
        return types.SimpleNamespace(out=o, err=e)


class _Monkeypatch:
    def __init__(self):
        self.undo = []

    def setattr(self, obj, name, value):
        self.undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, old in reversed(self.undo):
            setattr(obj, name, old)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def main() -> int:
    conftest = _load(HERE / "conftest.py")
    fixtures = {n: f for n, f in vars(conftest).items() if getattr(f, "_is_fixture", False)}
    failures = 0
    passed = 0
    for path in sorted(HERE.glob("test_*.py")):
        mod = _load(path)
        for name, fn in vars(mod).items():
            if not (name.startswith("test_") and callable(fn)):
                continue
            params = inspect.signature(fn).parameters
            tmp = Path(tempfile.mkdtemp(prefix="apidocgen-test-"))
            cap = _Capsys()
            mp = _Monkeypatch()
            gens = []
            kwargs = {}
            try:
                for p in params:
                    if p == "tmp_path":
                        kwargs[p] = tmp
                    elif p == "capsys":
                        kwargs[p] = cap
                    elif p == "monkeypatch":
                        kwargs[p] = mp
                    elif p in fixtures:
                        g = fixtures[p](tmp)
                        gens.append(g)
                        kwargs[p] = next(g)
                    else:
                        raise RuntimeError(f"unknown fixture {p}")
                cap.start()
                try:
                    fn(**kwargs)
                finally:
                    cap.stop()
                passed += 1
                print(f"PASS {path.name}::{name}")
            except _Skip:
                print(f"SKIP {path.name}::{name}")
            except Exception:
                failures += 1
                print(f"FAIL {path.name}::{name}\n{traceback.format_exc()}")
            finally:
                mp.restore()
                for g in gens:
                    try:
                        next(g)
                    except StopIteration:
                        pass
    print(f"\n{passed} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__" and pytest is None:
    sys.exit(main())
