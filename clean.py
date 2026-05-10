"""
clean.py — remove all generated/cache directories from the project root.

Usage:
    python clean.py

Removes:
    build/
    dist/
    *.egg-info/
    .pytest_cache/
    .mypy_cache/
    .ruff_cache/
    **/__pycache__/
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).parent

DIRS_TO_REMOVE = [
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
]

def clean():
    removed = []

    # Fixed directory names at root level
    for name in DIRS_TO_REMOVE:
        p = ROOT / name
        if p.exists():
            shutil.rmtree(p)
            removed.append(str(p.relative_to(ROOT)))

    # *.egg-info at root level
    for p in ROOT.glob("*.egg-info"):
        if p.is_dir():
            shutil.rmtree(p)
            removed.append(str(p.relative_to(ROOT)))

    # __pycache__ anywhere in the tree
    for p in ROOT.rglob("__pycache__"):
        if p.is_dir():
            shutil.rmtree(p)
            removed.append(str(p.relative_to(ROOT)))

    if removed:
        for r in removed:
            print(f"  removed  {r}")
        print(f"\n  {len(removed)} director{'y' if len(removed) == 1 else 'ies'} cleaned.")
    else:
        print("  Nothing to clean.")

if __name__ == "__main__":
    clean()
