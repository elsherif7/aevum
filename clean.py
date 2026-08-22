"""
Removes build artifacts created by `pip install .` / `pip install -e .`:

  build/            — setuptools build directory
  dist/             — wheel/sdist output (if you ever build one)
  *.egg-info/       — package metadata directory (e.g. aevum.egg-info)
  __pycache__/      — compiled bytecode caches, anywhere in the tree
  *.pyc / *.pyo     — stray compiled files, anywhere in the tree

Run from the project root:

    python3 clean.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _rm(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        print(f"  removed  {path.relative_to(ROOT)}/")
    elif path.is_file():
        path.unlink(missing_ok=True)
        print(f"  removed  {path.relative_to(ROOT)}")


def clean() -> None:
    # Top-level build artifacts
    for name in ("build", "dist"):
        _rm(ROOT / name)
    for egg_info in ROOT.glob("*.egg-info"):
        _rm(egg_info)

    # __pycache__ dirs and stray .pyc/.pyo files anywhere in the tree
    for cache_dir in ROOT.rglob("__pycache__"):
        _rm(cache_dir)
    for pyc in list(ROOT.rglob("*.pyc")) + list(ROOT.rglob("*.pyo")):
        _rm(pyc)

    print("\nDone.")


if __name__ == "__main__":
    clean()
