# Contributing to Aevum

Thanks for your interest in contributing. This document covers everything
you need to get started.

---

## Quick start

```bash
git clone https://github.com/elsherif7/aevum
cd aevum
pip install -e ".[dev]"
```

The `-e` flag installs in editable mode so your changes take effect
immediately without reinstalling.

---

## Requirements

- Python 3.10 or higher
- FFmpeg (includes `ffprobe`) on your PATH — https://ffmpeg.org/download.html
- Optional: `keyring` and `cryptography` for encrypted API key storage

---

## Running the tests

```bash
python -m pytest tests/ -v
```

All 135 tests must pass before submitting a pull request. Tests are pure
(no network calls, no ffprobe) and run in under 2 seconds.

---

## Linting and type checking

```bash
python -m ruff check aevum_pkg/   # lint
python -m mypy                    # type check
```

Both must report zero errors. The CI workflow enforces this on every push.

---

## Cleaning build artifacts

```bash
python clean.py
```

Removes `build/`, `dist/`, `*.egg-info/`, `.pytest_cache/`, `.mypy_cache/`,
`.ruff_cache/`, and all `__pycache__/` directories.

---

## Project structure

```
aevum_pkg/
  _models.py       — FolderNode and ScanTree data types
  _scan.py         — core scanning engine, native parsers, filters
  _display.py      — all terminal output functions
  _cache.py        — duration cache
  _config.py       — persistent configuration
  _history.py      — scan history and diff
  _export.py       — TXT/CSV/JSON/HTML export
  _dupes.py        — duplicate detection
  _compare.py      — folder comparison
  _youtube.py      — YouTube Data API v3 integration
  _apikey.py       — encrypted API key storage
  _paths.py        — platform-aware data directory resolution
  _color.py        — ANSI color singleton
  _exit.py         — CLI exit codes
  _cli.py          — entry point and dispatch
  _cli_args.py     — argparse definitions
  _cli_cmds.py     — one cmd_* function per subcommand
  _cli_helpers.py  — shared CLI helpers
  _cli_json.py     — JSON serialisers
  _cli_update.py   — self-update logic
```

---

## Code style

- **Formatter / linter**: ruff (`line-length = 120`)
- **Type checker**: mypy (all modules annotated, zero errors required)
- **Python version**: 3.10+ syntax throughout (`X | Y`, `list[X]`, etc.)
- **Imports**: sorted by ruff (`I` rules), `from __future__ import annotations`
  at the top of every annotated module
- **No shell=True**: all subprocess calls use list form
- **Atomic writes**: all persistent state uses temp file + `os.replace()`
- **No telemetry**: nothing is sent anywhere except the YouTube API

---

## Adding a new subcommand

1. Add the argparse definition to `_cli_args.py` in `_dispatch_subcommand()`
2. Add the command name to the `SUBCOMMANDS` tuple in `_cli_args.py`
3. Implement `cmd_<name>(args, cfg, use_json, quiet) -> None` in `_cli_cmds.py`
4. Add the dispatch entry to `_DISPATCH` in `_cli.py`
5. Document it in `README.md` (All commands section + relevant options table)
6. Add it to `CHANGELOG.md` under `[Unreleased]`

---

## Adding a new filter

Filters live in `_scan.py` (`apply_filters`) and are parsed in
`_cli_helpers.py` (`_build_filters`). Add the filter key to both functions
and wire up the argparse flag in every subcommand that should support it.

---

## Pull request checklist

Before opening a PR, make sure:

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `python -m ruff check aevum_pkg/` — zero errors
- [ ] `python -m mypy` — zero errors
- [ ] New behaviour is covered by at least one test
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] Commit messages follow the format: `type: short description`
  - Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`

---

## Reporting bugs

Open a GitHub issue with:
- The command you ran
- The full error output
- Your OS and Python version (`python --version`)
- Your FFmpeg version (`ffprobe -version`)

For security vulnerabilities, see [SECURITY.md](SECURITY.md) — please do
not open a public issue.

---

## License

By contributing, you agree that your contributions will be licensed under
the MIT License. See [LICENSE](LICENSE).
