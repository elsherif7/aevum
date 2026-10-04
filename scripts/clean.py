"""
Remove build artifacts left by `pip install` and `pip install -e`: build/, dist/,
*.egg-info, __editable__*.pth, and __pycache__ folders and .pyc/.pyo files, plus the
pytest, mypy and ruff caches.

    python3 scripts/clean.py
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _rm(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        print(f"  removed  {path.relative_to(ROOT)}/")
    elif path.is_file():
        path.unlink(missing_ok=True)
        print(f"  removed  {path.relative_to(ROOT)}")


def clean() -> None:
    for name in ("build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
        _rm(ROOT / name)
    for egg_info in ROOT.glob("*.egg-info"):
        _rm(egg_info)
    for pth in ROOT.glob("__editable__*.pth"):
        _rm(pth)

    for cache_dir in ROOT.rglob("__pycache__"):
        _rm(cache_dir)
    for pyc in list(ROOT.rglob("*.pyc")) + list(ROOT.rglob("*.pyo")):
        _rm(pyc)

    print("\nDone.")


if __name__ == "__main__":
    clean()
