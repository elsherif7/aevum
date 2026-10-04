"""
Run every check in one go: lint, type-check and the test suite.

    python3 scripts/check.py

To add a check, append a line to CHECKS. New test files are picked up by pytest on their own.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECKS = [
    ("ruff", ["ruff", "check", "."]),
    ("mypy", ["mypy"]),
    ("mypy .", ["mypy", "."]),
    ("pytest", ["pytest"]),
]


def run_check(name: str, args: list[str]) -> bool:
    print(f"\n=== {name} ===", flush=True)
    start = time.monotonic()
    try:
        ok = subprocess.run([sys.executable, "-m", *args], cwd=ROOT).returncode == 0
    except OSError as e:
        print(f"could not run {name}: {e}")
        ok = False
    print(f"--- {name}: {'ok' if ok else 'FAILED'} ({time.monotonic() - start:.1f}s)", flush=True)
    return ok


def main() -> int:
    results = [(name, run_check(name, args)) for name, args in CHECKS]
    print("\n=== Summary ===")
    for name, ok in results:
        print(f"  {'ok    ' if ok else 'FAILED'}  {name}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
