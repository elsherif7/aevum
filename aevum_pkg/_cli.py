"""
CLI entry point: argument parsing, subcommand dispatch, and main().
All business logic lives in the other modules.
"""
import argparse
import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path

from ._color   import clr, LINE, clear, _disable_color
from ._scan    import (check_ffprobe, format_duration, format_size, _run_scan,
                       parse_duration_arg, apply_filters, rebuild_after_filter)
from ._youtube import _is_url, scan_url, _make_url_progress, get_quota_status
from ._display import print_results, print_url_results, _fuzzy_suggest
from ._dupes   import find_duplicates, print_duplicates, print_dupe_warning, dupes_to_json
# ── Compare (inlined from _compare.py) ───────────────────────────────
def run_compare(folder_a, folder_b, on_progress, sort_by, use_cache):
    print(f"  {clr.DIM}Scanning {Path(folder_a).name}...{clr.RST}", end="", flush=True)
    sec_a, count_a, tree_a, dur_a, _, _ = _run_scan(folder_a, on_progress, sort_by, use_cache)
    print(f"\r  {clr.G}Done{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{Path(folder_a).name}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_a} files  {format_duration(sec_a)['hours_fmt']}{clr.RST}".ljust(70))
    print(f"  {clr.DIM}Scanning {Path(folder_b).name}...{clr.RST}", end="", flush=True)
    sec_b, count_b, tree_b, dur_b, _, _ = _run_scan(folder_b, on_progress, sort_by, use_cache)
    print(f"\r  {clr.G}Done{clr.RST}  {clr.DIM}\u2192{clr.RST}  {clr.W}{Path(folder_b).name}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_b} files  {format_duration(sec_b)['hours_fmt']}{clr.RST}".ljust(70))
    return (sec_a, count_a, dur_a), (sec_b, count_b, dur_b)


def print_comparison(folder_a, folder_b, data_a, data_b):
    sec_a, count_a, dur_a = data_a
    sec_b, count_b, dur_b = data_b
    name_a = Path(folder_a).name
    name_b = Path(folder_b).name
    delta_sec   = sec_b   - sec_a
    delta_count = count_b - count_a
    delta_sign  = "+" if delta_sec   >= 0 else ""
    delta_csign = "+" if delta_count >= 0 else ""
    subs_a  = {p.parent.name for p in dur_a}
    subs_b  = {p.parent.name for p in dur_b}
    only_a  = sorted(subs_a - subs_b)
    only_b  = sorted(subs_b - subs_a)
    in_both = sorted(subs_a & subs_b)
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.C}  Folder Comparison{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()
    print(f"  {clr.W}  {name_a:<30}{clr.RST}  {clr.Y}{format_duration(sec_a)['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_a} files{clr.RST}")
    print(f"  {clr.W}  {name_b:<30}{clr.RST}  {clr.Y}{format_duration(sec_b)['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {clr.Y}{count_b} files{clr.RST}")
    print()
    delta_col = clr.G if delta_sec >= 0 else clr.R
    print(f"  {clr.W}  Delta{{'':<25}}{clr.RST}  {delta_col}{delta_sign}{format_duration(abs(delta_sec))['hours_fmt']}{clr.RST}  {clr.DIM}|{clr.RST}  {delta_col}{delta_csign}{delta_count} files{clr.RST}")
    print()
    if only_a:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.Y}  Only in {name_a}{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for s in only_a:
            print(f"    {clr.DIM}\u2192{clr.RST}  {s}")
        print()
    if only_b:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.Y}  Only in {name_b}{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for s in only_b:
            print(f"    {clr.DIM}\u2192{clr.RST}  {s}")
        print()
    if in_both:
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.G}  In both{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        for s in in_both:
            print(f"    {clr.DIM}\u2192{clr.RST}  {s}")
        print()
from ._export  import export_results, export_url_results
from ._config  import (CONFIG_DEFAULTS, load_config, save_config,
                       cmd_doctor, cmd_cache, cmd_config)
from ._youtube import load_api_key, prompt_api_key
from ._paths   import CACHE_DIR
# ── Exit codes (inlined from _exit.py) ───────────────────────────────
class EX:
    OK         = 0
    ERR_ARGS   = 1
    ERR_DEPS   = 2
    ERR_SCAN   = 3
    ERR_EXPORT = 4
    ERR_API    = 5

__version__ = "1.0.0"


# ── JSON OUTPUT ───────────────────────────────────────────────────────

def _json_out(data: dict):
    """Write JSON to stdout and flush."""
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def _json_error(msg: str, code: int, extra: dict = None):
    d = {"status": "error", "code": code, "error": msg}
    if extra:
        d.update(extra)
    _json_out(d)
    sys.exit(code)


def _run_pip_upgrade(src_dir, quiet=False):
    """
    Run pip install --upgrade in a background thread with an animated bar.

    Issue 21 fix: error output from the subprocess is now passed out of the
    thread via a plain list (_err) instead of a function attribute
    (_worker.err), which was unconventional and fragile.
    """
    import subprocess as _sp
    import threading

    pip_cmd = [sys.executable, "-m", "pip", "install", str(src_dir), "--upgrade", "-q"]
    if quiet:
        return _sp.run(pip_cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL).returncode

    _frames = [
        "████░░░░░░░░░░░░░░░░░░░░", "████████░░░░░░░░░░░░░░░░",
        "████████████░░░░░░░░░░░░", "████████████████░░░░░░░░",
        "████████████████████░░░░", "████████████████████████",
    ]
    _done = threading.Event()
    _rc   = [0]
    _err  = [""]   # Issue 21: use a list, not a function attribute

    def _worker():
        r      = _sp.run(pip_cmd, stdout=_sp.DEVNULL, stderr=_sp.PIPE)
        _rc[0] = r.returncode
        _err[0] = r.stderr.decode(errors="replace").strip() if r.returncode != 0 else ""
        _done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    fi = 0
    while not _done.wait(timeout=0.2):
        print(f"\r  {clr.C}Installing...{clr.RST}  {clr.Y}{_frames[fi % len(_frames)]}{clr.RST}  ",
              end="", flush=True)
        fi += 1
    t.join()
    if _rc[0] == 0:
        print(f"\r  {clr.G}Done!{clr.RST}          {clr.G}{'█' * 24}{clr.RST}  ")
        print(f"\n  {clr.G}[OK]{clr.RST}  Aevum updated successfully.\n")
    else:
        print(f"\r  {clr.R}[FAIL]{clr.RST} pip install failed (exit {_rc[0]}).  ")
        for line in _err[0].splitlines()[-6:]:
            print(f"  {clr.DIM}{line}{clr.RST}")
        print()
    return _rc[0]


def _open_appdata():
    import subprocess as _sp
    from ._paths import APPDATA
    APPDATA.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        _sp.Popen(["explorer", str(APPDATA)])
    else:
        _sp.Popen(["xdg-open", str(APPDATA)])
    return APPDATA


def _scan_to_json(folder, total_sec, total_count, tree, durations, sizes, hits):
    """Convert a completed local scan to a JSON-serialisable dict."""
    from ._export import _tree_to_dict
    subfolders, direct, root_bytes = tree
    fmt = format_duration(total_sec)
    return {
        "status":      "ok",
        "command":     "scan",
        "path":        str(Path(folder).resolve()),
        "total_files": total_count,
        "total_bytes": sum(sizes.values()),
        "total_sec":   round(total_sec, 2),
        "duration":    fmt,
        "cache_hits":  hits,
        "tree":        _tree_to_dict(Path(folder).name, total_sec, total_count, subfolders, direct),
        "files": [
            {
                "path":     str(p),
                "filename": p.name,
                "folder":   p.parent.name,
                "seconds":  round(s, 2),
                "bytes":    sizes.get(p, 0),
                "duration": format_duration(s)["hours_fmt"],
            }
            for p, s in sorted(durations.items(), key=lambda x: x[1], reverse=True)
        ],
    }


def _url_to_json(url, label, total_sec, total_count, entries):
    fmt = format_duration(total_sec)
    return {
        "status":      "ok",
        "command":     "scan",
        "url":         url,
        "label":       label,
        "total_files": total_count,
        "total_sec":   round(total_sec, 2),
        "duration":    fmt,
        "videos": [
            {
                "title":    e["title"],
                "channel":  e.get("channel", ""),
                "url":      e.get("url", ""),
                "seconds":  round(e["duration"], 2),
                "duration": format_duration(e["duration"])["hours_fmt"],
            }
            for e in sorted(entries, key=lambda x: x["duration"], reverse=True)
        ],
    }


def _dupes_to_json(groups, durations, sizes):
    """
    Issue 16 fix: wasted-time calculation now delegates to dupes_to_json()
    in _dupes.py which uses the median-based formula — same as print_duplicates()
    — so --json output matches human output exactly.
    """
    from ._dupes import dupes_to_json as _dj
    entries      = _dj(groups, durations)
    total_wasted = sum(e["wasted_sec"] for e in entries)
    # Remap keys for the legacy CLI JSON shape.
    out_groups = []
    for e, group in zip(entries, groups):
        out_groups.append({
            "copies":      e["copies"],
            "seconds":     e["duration_sec"],
            "wasted_sec":  e["wasted_sec"],
            "wasted_fmt":  format_duration(e["wasted_sec"])["hours_fmt"],
            "files": [{"path": str(p), "bytes": sizes.get(p, 0)} for p in group],
        })
    return {
        "status":           "ok",
        "command":          "dupes",
        "groups_found":     len(groups),
        "total_wasted_sec": round(total_wasted, 2),
        "total_wasted_fmt": format_duration(total_wasted)["hours_fmt"],
        "groups":           out_groups,
    }


def _compare_to_json(folder_a, folder_b, data_a, data_b):
    sec_a, count_a, _ = data_a
    sec_b, count_b, _ = data_b
    delta = sec_b - sec_a
    return {
        "status":  "ok",
        "command": "compare",
        "a": {
            "path":        str(Path(folder_a).resolve()),
            "total_files": count_a,
            "total_sec":   round(sec_a, 2),
            "duration":    format_duration(sec_a)["hours_fmt"],
        },
        "b": {
            "path":        str(Path(folder_b).resolve()),
            "total_files": count_b,
            "total_sec":   round(sec_b, 2),
            "duration":    format_duration(sec_b)["hours_fmt"],
        },
        "delta": {
            "seconds":  round(delta, 2),
            "duration": format_duration(abs(delta))["hours_fmt"],
            "sign":     "+" if delta >= 0 else "-",
            "files":    count_b - count_a,
        },
    }


# ── HELPERS ───────────────────────────────────────────────────────────

def _make_progress_bar(quiet=False, use_json=False):
    """
    Return a progress callback, or None in machine-output modes.

    Issue 15 fix: guard against total == 0 inside the callback itself so
    that any caller passing total=0 directly gets a no-op instead of a
    ZeroDivisionError.
    """
    if quiet or use_json:
        return None

    def on_progress(done, total):
        if total <= 0:   # Issue 15
            return
        pct    = int((done / total) * 100)
        filled = int(24 * done / total)
        bar    = "█" * filled + "░" * (24 - filled)
        print(f"\r  {clr.C}Scanning...{clr.RST}  {bar}  {clr.Y}{done}/{total}{clr.RST}  {clr.DIM}({pct}%){clr.RST}",
              end='', flush=True)

    return on_progress


def _require_ffprobe(context="", use_json=False):
    if not check_ffprobe():
        ctx = f" ({context})" if context else ""
        if use_json:
            _json_error(
                f"ffprobe not found on PATH{ctx}. Install FFmpeg: https://ffmpeg.org/download.html",
                EX.ERR_DEPS,
            )
        print(f"\n  {clr.R}[ERROR]{clr.RST} ffprobe not found on PATH{ctx}.", file=sys.stderr)
        print(f"  {clr.DIM}ffprobe is required for local folder scanning.{clr.RST}", file=sys.stderr)
        print(f"  Install FFmpeg: {clr.C}https://ffmpeg.org/download.html{clr.RST}", file=sys.stderr)
        print(f"  Then re-run:    {clr.W}aevum doctor{clr.RST}\n", file=sys.stderr)
        sys.exit(EX.ERR_DEPS)


def _resolve_sort(args, cfg):
    raw = getattr(args, 'sort', None) or cfg.get('sort') or 'name:asc'
    if ':' not in raw:
        defaults = {'name': 'asc', 'duration': 'desc', 'count': 'desc'}
        raw = raw + ':' + defaults.get(raw, 'asc')
    return raw


def _resolve_top(args, cfg):
    v = getattr(args, 'top', None)
    return v if v is not None else cfg.get('top', 10)


def _resolve_out_format(out_path, explicit_fmt):
    if explicit_fmt:
        return explicit_fmt
    if out_path:
        ext = Path(out_path).suffix.lower().lstrip('.')
        if ext in ('txt', 'csv', 'json'):
            return ext
    return None


def _use_cache(args, cfg):
    """
    Issue 33 fix: single authoritative helper for the use_cache flag so that
    all callers derive it the same way. The no_cache arg may not be present
    on all namespaces, so we use getattr with a default.
    """
    if getattr(args, 'no_cache', False):
        return False
    return cfg.get('cache_enabled', True)


def _resolve_alias(raw, cfg):
    """
    If raw matches a known alias (case-insensitive), return the expansion.
    Otherwise return raw unchanged.

    Aliases can now hold any text — a folder path, a flag, a flag+value,
    or even a whole command fragment.  The expansion is always returned as
    a list of tokens so callers that need multi-token expansion can use it
    directly (e.g. `--speed 0.5` → ['--speed', '0.5']).

    For backwards compatibility, callers that only pass a single string and
    just need a path still get a plain string back when the expansion is a
    single token.  Multi-token expansions are returned as a list.

    Issue 17 fix: empty string is returned immediately.
    """
    if not raw:
        return raw
    aliases = cfg.get("aliases") or {}
    expanded = aliases.get(raw) or aliases.get(raw.upper()) or aliases.get(raw.lower())
    if expanded is None:
        return raw
    # Split into tokens so "--speed 0.5" works correctly
    import shlex
    try:
        tokens = shlex.split(expanded)
    except ValueError:
        tokens = expanded.split()
    return tokens if len(tokens) > 1 else (tokens[0] if tokens else raw)


def _expand_aliases_in_argv(argv, cfg):
    """
    Walk argv and replace any token that matches an alias with its expansion.
    Tokens that start with '-' are never treated as alias names.
    This runs before argparse so every subcommand benefits automatically.
    """
    aliases = cfg.get("aliases") or {}
    result = []
    for tok in argv:
        if tok.startswith('-'):
            result.append(tok)
            continue
        expanded = aliases.get(tok) or aliases.get(tok.upper()) or aliases.get(tok.lower())
        if expanded is not None:
            import shlex
            try:
                result.extend(shlex.split(expanded))
            except ValueError:
                result.extend(expanded.split())
        else:
            result.append(tok)
    return result


# ── UPDATE LOGIC ──────────────────────────────────────────────────────
# Issue 18 fix: extracted into a shared function so the headless 'update'
# command uses a single clean implementation.

def _do_update(cfg, dry_run=False, quiet=False):
    """
    Core update flow.  Returns the pip exit code, or 0 on early exit.
    Mutates cfg['project_dir'] if the user enters a new path.
    """
    def _find_project_dir():
        saved = cfg.get('project_dir', '')
        if saved and (Path(saved) / "pyproject.toml").exists():
            return Path(saved)
        if (Path.cwd() / "pyproject.toml").exists():
            return Path.cwd()
        return None

    src_dir = _find_project_dir()

    # Warn if a saved path exists but no longer resolves — _find_project_dir()
    # already returns None in that case so we just need the warning message.
    saved = cfg.get('project_dir', '')
    if saved and not (Path(saved) / "pyproject.toml").exists():
        print(f"\n  {clr.Y}[WARN]{clr.RST}  Saved project path no longer exists: {clr.W}{saved}{clr.RST}")
        print(f"  The project folder may have moved or been renamed.")
        # Do NOT force src_dir = None here: _find_project_dir() may have
        # already found a valid fallback via cwd, so we keep its result.

    if src_dir is None:
        print(f"\n  {clr.Y}Aevum project folder not found.{clr.RST}")
        print(f"  {clr.DIM}Option 1:{clr.RST}  cd to your Aevum folder and run {clr.W}aevum update{clr.RST} from there.")
        print(f"  {clr.DIM}Option 2:{clr.RST}  Paste the path to your Aevum folder below.")
        print()
        try:
            pasted = input(f"  {clr.C}Aevum folder path{clr.RST} (or Enter to cancel)> ").strip().strip("'\"")
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if not pasted:
            return 0
        src_dir = Path(pasted)
        if not (src_dir / "pyproject.toml").exists():
            print(f"\n  {clr.R}[ERROR]{clr.RST} No pyproject.toml found at {src_dir}.\n", file=sys.stderr)
            return EX.ERR_ARGS
        cfg['project_dir'] = str(src_dir)
        save_config(cfg)
        print(f"  {clr.G}[OK]{clr.RST}  Path saved. You can run {clr.W}aevum update{clr.RST} from anywhere now.\n")

    pip_cmd = [sys.executable, "-m", "pip", "install", str(src_dir), "--upgrade", "-q"]
    if dry_run:
        print(f"  {clr.DIM}Would run:{clr.RST}  {clr.W}{' '.join(pip_cmd)}{clr.RST}")
        return 0
    if not quiet:
        print(f"  {clr.W}Upgrading Aevum from{clr.RST}  {clr.C}{src_dir}{clr.RST}\n")
    return _run_pip_upgrade(src_dir, quiet=quiet)


# ── ARG PARSING ───────────────────────────────────────────────────────

def _print_global_help():
    print(f"""
  {clr.C}aevum {__version__}{clr.RST}  {clr.DIM}—{clr.RST}  {clr.W}Media Library Scanner{clr.RST}

  {clr.W}USAGE{clr.RST}
    aevum [command] [options]
    aevum <path|url>                Quick scan (shorthand for 'aevum scan')

  {clr.W}COMMANDS{clr.RST}
    scan      <path|url>            Scan a folder or YouTube URL
    compare   <path> <path>         Compare two libraries side-by-side
    dupes     <path>                Find duplicate files (by size + hash)
    export    <path|url> <format>   Scan and write results to a file
    watch     <path>                Re-scan automatically when folder changes
    files     <path>                Scan and show all files under each folder
    alias                           Manage aliases (paths, flags, or any shorthand)
    cache                           Manage the duration cache
    config                          Read/write configuration
    quota                           Check YouTube API quota usage
    doctor                          Check environment (ffprobe, API key, cache)
    update                          Upgrade Aevum to the latest version
    clearpath                       Clear saved project path for updates
    appdata                         Open the Aevum data folder
    version                         Print version and exit

  {clr.W}GLOBAL OPTIONS{clr.RST}
    --no-color                      Disable ANSI color output
    --json                          Machine-readable JSON output to stdout
    -q, --quiet                     Suppress decorative output (errors → stderr only)
    -h, --help                      Show this help
    -V, --version                   Show version
    -U, --upgrade                   Alias for 'aevum update'

  {clr.W}EXIT CODES{clr.RST}
    0  success
    1  bad arguments / path not found
    2  missing dependency (ffprobe)
    3  scan error / interrupted
    4  export / write failed
    5  YouTube API error

  {clr.DIM}Run 'aevum <command> --help' for command-specific options.{clr.RST}
""")


def _add_common_flags(p):
    """Attach --no-color / --json / --quiet to any subcommand parser."""
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    p.add_argument("--json",     action="store_true", help="Output JSON to stdout")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress all decorative output; only errors go to stderr")


def _parse_args():
    argv = sys.argv[1:]

    # On Windows, "D:\" can arrive as two tokens: ["D:", "\"] — rejoin them.
    rejoined = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.endswith(':') and i + 1 < len(argv) and argv[i + 1] in ('\\', '/'):
            rejoined.append(tok + argv[i + 1])
            i += 2
        else:
            rejoined.append(tok)
            i += 1
    argv = rejoined

    if not argv:
        _print_global_help()
        sys.exit(EX.OK)

    if argv[0] in ('-h', '--help'):
        _print_global_help()
        sys.exit(EX.OK)
    if argv[0] in ('-V', '--version'):
        print(f"aevum {__version__}")
        sys.exit(EX.OK)
    if argv[0] in ('-U', '--upgrade'):
        argv = ['update'] + argv[1:]

    # Expand aliases early so every subcommand benefits.
    # We load config here just for alias expansion; main() reloads it normally.
    try:
        _early_cfg = load_config()
        argv = _expand_aliases_in_argv(argv, _early_cfg)
    except Exception:
        pass  # never let alias expansion crash startup

    SUBCOMMANDS = (
        'scan', 'compare', 'dupes', 'export', 'watch', 'cache', 'config',
        'alias', 'doctor', 'quota', 'version', 'update', 'clearpath',
        'appdata', 'files',
    )

    # Flags that consume the next token as their value.
    _VALUE_FLAGS = {
        '--speed', '-s', '--sort', '-t', '--top', '-o', '--out',
        '--format', '--depth', '-i', '--interval',
        '--min-duration', '--max-duration', '--ext', '--folder',
    }

    # If no command word was given (e.g. aevum --speed 0.5 D:\path or
    # aevum D:\path) find the first non-flag token to use as the command
    # or path.  Flags and their values are collected separately.
    flags_before = []
    first_pos    = None   # first non-flag token index in original argv
    _j = 0
    while _j < len(argv):
        tok = argv[_j]
        if tok.startswith('-'):
            flags_before.append(tok)
            bare = tok.split('=')[0]
            if '=' not in tok and bare in _VALUE_FLAGS and _j + 1 < len(argv):
                _j += 1
                flags_before.append(argv[_j])
        else:
            first_pos = _j
            break
        _j += 1

    if first_pos is None:
        # only flags, no command or path
        _print_global_help()
        sys.exit(EX.OK)

    # Rebuild argv with flags moved after the first positional token
    if flags_before:
        argv = argv[first_pos:first_pos+1] + flags_before + argv[first_pos+1:]

    subcommand = argv[0]

    if subcommand not in SUBCOMMANDS:
        suggestion = _fuzzy_suggest(subcommand, list(SUBCOMMANDS))
        if (subcommand.startswith(('/', '\\', '.')) or
                ':' in subcommand or
                subcommand.startswith(('http://', 'https://', 'www.'))):
            # No command word — treat as shorthand scan.
            # Pair each flag with its value so path tokens are isolated.
            flags      = []
            path_parts = []
            _k = 0
            while _k < len(argv):
                t = argv[_k]
                if t.startswith('-'):
                    flags.append(t)
                    bare = t.split('=')[0]
                    if '=' not in t and bare in _VALUE_FLAGS and _k + 1 < len(argv):
                        _k += 1
                        flags.append(argv[_k])
                else:
                    path_parts.append(t)
                _k += 1
            if len(path_parts) == 1:
                argv = ['scan', path_parts[0]] + flags
            else:
                argv = ['scan'] + path_parts + flags
            subcommand = 'scan'
        elif suggestion:
            print(f"\n  {clr.R}aevum: '{subcommand}' is not a command.{clr.RST}  "
                  f"{clr.DIM}Did you mean{clr.RST}  {clr.W}{suggestion}{clr.RST}{clr.DIM}?{clr.RST}\n")
            sys.exit(EX.ERR_ARGS)
        else:
            print(f"\n  {clr.R}aevum: '{subcommand}' is not a command.{clr.RST}  "
                  f"{clr.DIM}Run{clr.RST}  {clr.W}aevum --help{clr.RST}  "
                  f"{clr.DIM}for a list of commands.{clr.RST}\n")
            sys.exit(EX.ERR_ARGS)

    return _dispatch_subcommand(subcommand, argv[1:])


def _dispatch_subcommand(sub, argv):
    if sub == 'files':
        p = argparse.ArgumentParser(prog="aevum files",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a folder and show every video file listed under its folder.",
            epilog="Examples:\n  aevum files D:\\Movies\n  aevum files D:\\Movies --sort name\n")
        p.add_argument("folder_parts", nargs="*", metavar="PATH")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--speed", dest="speeds", type=float, action="append",
                       default=None, metavar="SPEED")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.folder  = ' '.join(args.folder_parts)
        args.command = sub
        return args

    if sub == 'scan':
        p = argparse.ArgumentParser(prog="aevum scan",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a local folder or YouTube URL and report total duration.",
            epilog=("Examples:\n"
                    "  aevum scan D:\\Movies\n"
                    "  aevum scan D:\\Movies --sort duration --top 20\n"
                    "  aevum scan D:\\Movies --json\n"
                    "  aevum scan https://youtube.com/@mkbhd --json\n"))
        p.add_argument("targets", nargs="*", default=[], metavar="PATH|URL")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("-f", "--files", action="store_true")
        p.add_argument("-o", "--out",   default=None, metavar="FILE")
        p.add_argument("--format", dest="fmt", choices=["txt", "csv", "json"], default=None)
        p.add_argument("--depth",  type=int, default=None, metavar="N")
        p.add_argument("--no-cache", action="store_true")
        p.add_argument("--merge", action="store_true",
                       help="Aggregate all targets into one combined grand total")
        p.add_argument("--min-duration", default=None, metavar="DURATION")
        p.add_argument("--max-duration", default=None, metavar="DURATION")
        p.add_argument("--ext", default=None, metavar="EXT[,EXT]")
        p.add_argument("--folder", dest="folder_pat", default=None, metavar="PATTERN")
        p.add_argument("--speed", dest="speeds", type=float, action="append",
                       default=None, metavar="SPEED")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'watch':
        p = argparse.ArgumentParser(prog="aevum watch",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Re-scan a folder automatically whenever its contents change.",
            epilog=("Examples:\n"
                    "  aevum watch D:\\Movies\n"
                    "  aevum watch D:\\Movies --interval 10\n"
                    "  aevum watch D:\\Downloads --ext mkv,mp4 --min-duration 5m\n"))
        p.add_argument("folder", metavar="PATH")
        p.add_argument("-i", "--interval", type=float, default=5.0, metavar="SECONDS")
        p.add_argument("--no-clear", action="store_true")
        p.add_argument("-s", "--sort",  default=None, metavar="FIELD[:DIR]")
        p.add_argument("-t", "--top",   type=int, default=None, metavar="N")
        p.add_argument("--min-duration", default=None, metavar="DURATION")
        p.add_argument("--max-duration", default=None, metavar="DURATION")
        p.add_argument("--ext", default=None, metavar="EXT[,EXT]")
        p.add_argument("--folder", dest="folder_pat", default=None, metavar="PATTERN")
        p.add_argument("--speed", dest="speeds", type=float, action="append",
                       default=None, metavar="SPEED")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'compare':
        p = argparse.ArgumentParser(prog="aevum compare",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Compare the duration totals of two local libraries.",
            epilog="Examples:\n  aevum compare D:\\Movies E:\\Movies-Backup\n")
        p.add_argument("folder_a")
        p.add_argument("folder_b")
        p.add_argument("-s", "--sort", default=None, metavar="FIELD[:DIR]")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'dupes':
        p = argparse.ArgumentParser(prog="aevum dupes",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Find duplicate files (by size + partial hash) in a folder.",
            epilog="Examples:\n  aevum dupes D:\\Movies\n  aevum dupes D:\\Movies --json\n")
        p.add_argument("folder")
        p.add_argument("-o", "--out", default=None, metavar="FILE")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'export':
        p = argparse.ArgumentParser(prog="aevum export",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Scan a folder or URL and write results directly to a file.",
            epilog="Examples:\n  aevum export D:\\Movies csv\n  aevum export D:\\Movies json -o library.json\n")
        p.add_argument("target", metavar="PATH|URL")
        p.add_argument("format", choices=["txt", "csv", "json"])
        p.add_argument("-o", "--out",  default=None, metavar="FILE")
        p.add_argument("-s", "--sort", default=None, metavar="FIELD[:DIR]")
        p.add_argument("--no-cache", action="store_true")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'cache':
        p = argparse.ArgumentParser(prog="aevum cache",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Manage the duration cache.",
            epilog=("Subcommands:\n"
                    "  list          List all cache files\n"
                    "  clear         Delete all cache files\n"
                    "  clear <path>  Delete cache for a specific folder\n"
                    "  path          Print the cache directory path\n"))
        p.add_argument("action", nargs="?", default="list", choices=["list", "clear", "path"])
        p.add_argument("folder", nargs="?", default=None)
        p.add_argument("--no-color", action="store_true")
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'config':
        p = argparse.ArgumentParser(prog="aevum config",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Read and write persistent configuration.",
            epilog=("Keys:  sort  top  no_color  cache_enabled  export_dir  yt_api_key\n\n"
                    "Examples:\n"
                    "  aevum config list\n"
                    "  aevum config set sort duration:desc\n"
                    "  aevum config set yt_api_key AIzaSy...\n"))
        p.add_argument("action", choices=["get", "set", "list", "reset"])
        p.add_argument("key",   nargs="?", default=None)
        p.add_argument("value", nargs="?", default=None)
        p.add_argument("--no-color", action="store_true")
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'update':
        p = argparse.ArgumentParser(prog="aevum update",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Upgrade Aevum to the latest version via pip.",
            epilog="Examples:\n  aevum update\n  aevum -U\n")
        p.add_argument("--dry-run", action="store_true",
                       help="Show what would run without actually upgrading")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'alias':
        p = argparse.ArgumentParser(prog="aevum alias",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Manage aliases — shortcuts for paths, flags, or any text.",
            epilog=("Examples:\n"
                    "  aevum alias list\n"
                    "  aevum alias set M D:\\02-Media\n"
                    "  aevum alias set sp \"--speed 0.5\"\n"
                    "  aevum alias set top20 \"--top 20\"\n"
                    "  aevum alias remove M\n"
                    "  aevum scan M sp\n"
                    "  aevum scan M top20\n"))
        p.add_argument("action", nargs="?", default="list", choices=["list", "set", "remove", "rm"])
        p.add_argument("name",   nargs="?", default=None)
        p.add_argument("path",   nargs="?", default=None)
        p.add_argument("--no-color", action="store_true")
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'doctor':
        p = argparse.ArgumentParser(prog="aevum doctor",
            description="Check environment: ffprobe, API key, cache, Python version.")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'quota':
        p = argparse.ArgumentParser(prog="aevum quota",
            description="Check YouTube API quota usage for today.")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    if sub == 'version':
        print(f"aevum {__version__}")
        sys.exit(EX.OK)

    if sub == 'appdata':
        return types.SimpleNamespace(command='appdata', no_color=False, json=False, quiet=False)

    if sub == 'clearpath':
        p = argparse.ArgumentParser(prog="aevum clearpath",
            description="Clear the saved Aevum project path used by 'aevum update'.")
        _add_common_flags(p)
        args = p.parse_intermixed_args(argv)
        args.command = sub
        return args

    _print_global_help()
    sys.exit(EX.ERR_ARGS)


# ── MAIN ──────────────────────────────────────────────────────────────

def _build_filters(args, use_json=False):
    """
    Parse filter-related args into a filters dict for apply_filters().
    Returns {} if no filters were requested.
    Exits with ERR_ARGS on bad input.
    """
    filters = {}
    for attr, key in (('min_duration', 'min_duration'), ('max_duration', 'max_duration')):
        raw = getattr(args, attr, None)
        if raw:
            try:
                filters[key] = parse_duration_arg(raw)
            except ValueError as e:
                if use_json:
                    _json_error(str(e), EX.ERR_ARGS)
                print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
    raw_ext = getattr(args, 'ext', None)
    if raw_ext:
        filters['exts'] = {
            ('.' + x.lstrip('.').lower()) for x in raw_ext.split(',') if x.strip()
        }
    folder_pat = getattr(args, 'folder_pat', None)
    if folder_pat:
        filters['folder_pat'] = folder_pat
    return filters


def main():
    args = _parse_args()
    cfg  = load_config()

    use_json = getattr(args, 'json', False)
    quiet    = getattr(args, 'quiet', False) or use_json

    if getattr(args, 'no_color', False) or cfg.get('no_color') or use_json:
        _disable_color()

    cmd = args.command

    # ── version ───────────────────────────────────────────────────────
    if cmd == 'version':
        if use_json:
            _json_out({"status": "ok", "version": __version__})
        else:
            print(f"aevum {__version__}")
        sys.exit(EX.OK)

    # ── update ────────────────────────────────────────────────────────
    if cmd == 'update':
        rc = _do_update(cfg,
                        dry_run=getattr(args, 'dry_run', False),
                        quiet=quiet)
        sys.exit(rc)

    # ── clearpath ─────────────────────────────────────────────────────
    if cmd == 'clearpath':
        if 'project_dir' in cfg:
            cleared = cfg['project_dir']
            del cfg['project_dir']
            save_config(cfg)
            if use_json:
                _json_out({"status": "ok", "message": "Saved path cleared", "path": cleared})
            else:
                print(f"  {clr.G}[OK]{clr.RST}  Saved path cleared: {clr.DIM}{cleared}{clr.RST}")
        else:
            if use_json:
                _json_out({"status": "ok", "message": "No saved path to clear"})
            else:
                print(f"  {clr.DIM}No saved path to clear.{clr.RST}")
        sys.exit(EX.OK)

    # ── appdata ───────────────────────────────────────────────────────
    if cmd == 'appdata':
        folder = _open_appdata()
        if use_json:
            _json_out({"status": "ok", "path": str(folder)})
        else:
            print(f"  {clr.G}[OK]{clr.RST}  Opened  {clr.W}{folder}{clr.RST}")
        sys.exit(EX.OK)

    # ── alias ─────────────────────────────────────────────────────────
    if cmd == 'alias':
        aliases  = cfg.setdefault("aliases", {})
        action   = getattr(args, 'action', 'list') or 'list'
        name     = getattr(args, 'name', None)
        path_val = getattr(args, 'path', None)

        if action == 'list':
            if not aliases:
                print(f"  {clr.DIM}No aliases defined.{clr.RST}  "
                      f"Add one with: {clr.W}aevum alias set <name> <value>{clr.RST}\n")
                sys.exit(EX.OK)

            _SUBCOMMANDS = {
                'scan', 'compare', 'dupes', 'export', 'watch', 'cache',
                'config', 'alias', 'doctor', 'quota', 'version', 'update',
                'clearpath', 'appdata', 'files',
            }

            def _alias_type(v):
                import shlex
                try:
                    tokens = shlex.split(v)
                except ValueError:
                    tokens = v.split()
                if not tokens:
                    return 'unknown', None
                first = tokens[0]
                _is_path = (
                    first.startswith(('/', '\\', '.')) or
                    (len(first) >= 2 and first[1] == ':')
                )
                if _is_path:
                    exists = Path(v.strip("\'\"")).exists()
                    return 'path', exists
                if first.lower() in _SUBCOMMANDS:
                    return 'command', None
                if any(t.startswith('-') for t in tokens):
                    return 'flag', None
                return 'unknown', None

            print()
            print(f"  {clr.C}{LINE}{clr.RST}")
            print(f"  {clr.W}  Aliases{clr.RST}")
            print(f"  {clr.C}{LINE}{clr.RST}")
            for k, v in sorted(aliases.items()):
                kind, extra = _alias_type(v)
                if kind == 'path':
                    type_label = f"{clr.B}[path]{clr.RST}"
                    ok_label   = f"  {clr.G}\u2713{clr.RST}" if extra else f"  {clr.R}\u2717 not found{clr.RST}"
                    status = f"{type_label}{ok_label}"
                elif kind == 'command':
                    status = f"{clr.M}[command]{clr.RST}"
                elif kind == 'flag':
                    status = f"{clr.C}[flag]{clr.RST}"
                else:
                    status = f"{clr.R}[unknown]{clr.RST}  {clr.DIM}not a command, flag, or path{clr.RST}"
                print(f"  {clr.G}{k:<15}{clr.RST}  {clr.W}{v:<35}{clr.RST}  {status}")
            print()
            sys.exit(EX.OK)

        if action in ('remove', 'rm'):
            if not name:
                print(f"  {clr.R}[ERROR]{clr.RST} Alias name required.", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
            if name not in aliases:
                print(f"  {clr.Y}[WARN]{clr.RST}  Alias '{name}' not found.")
                sys.exit(EX.OK)
            del aliases[name]
            save_config(cfg)
            print(f"  {clr.G}[OK]{clr.RST}  Alias '{name}' removed.")
            sys.exit(EX.OK)

        if action == 'set':
            if not name or not path_val:
                print(f"  {clr.R}[ERROR]{clr.RST} Usage: aevum alias set <name> <value>", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
            # Accept any value — paths, flags, commands, or combinations.
            # Only warn about non-existent paths when the value actually looks
            # like a filesystem path (contains a separator or drive letter).
            value = path_val.strip().strip("'\"")
            _looks_like_path = (
                value.startswith(('/', '\\', '.')) or
                (len(value) >= 2 and value[1] == ':')  # Windows drive, e.g. D:
            )
            if _looks_like_path and not Path(value).exists():
                print(f"  {clr.Y}[WARN]{clr.RST}  Path does not exist: {value}")
            aliases[name] = value
            save_config(cfg)
            print(f"  {clr.G}[OK]{clr.RST}  {clr.W}{name}{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{value}{clr.RST}")
            sys.exit(EX.OK)

        sys.exit(EX.OK)

    # ── doctor ────────────────────────────────────────────────────────
    if cmd == 'doctor':
        if use_json:
            import subprocess as _sp
            ffprobe_ok = check_ffprobe()
            try:
                r = _sp.run(['ffprobe', '-version'], capture_output=True, text=True)
                ffprobe_ver = r.stdout.splitlines()[0] if r.stdout else None
            except Exception:
                ffprobe_ver = None
            api_key = load_api_key()
            try:
                files       = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
                cache_bytes = sum(f.stat().st_size for f in files)
            except Exception:
                files = []; cache_bytes = 0
            _json_out({
                "status":          "ok",
                "command":         "doctor",
                "python":          sys.version.split()[0],
                "ffprobe":         ffprobe_ok,
                "ffprobe_version": ffprobe_ver,
                "yt_api_key_set":  bool(api_key),
                "cache_files":     len(files),
                "cache_bytes":     cache_bytes,
                "cache_dir":       str(CACHE_DIR),
            })
            sys.exit(EX.OK)
        cmd_doctor(cfg)
        sys.exit(EX.OK)

    # ── config ────────────────────────────────────────────────────────
    if cmd == 'config':
        cmd_config(args, cfg)
        sys.exit(EX.OK)

    # ── quota ─────────────────────────────────────────────────────────
    if cmd == 'quota':
        api_key = load_api_key()
        if not api_key:
            if use_json:
                _json_out({"status": "error", "error": "No YouTube API key set"})
            else:
                print(f"\n  {clr.R}[ERROR]{clr.RST} No YouTube API key set.\n")
                print(f"  Set it with: {clr.W}aevum config set yt_api_key <key>{clr.RST}\n")
            sys.exit(EX.ERR_ARGS)
        used, remaining, pct = get_quota_status()
        if use_json:
            _json_out({
                "status":           "ok",
                "command":          "quota",
                "quota_used":       used,
                "quota_remaining":  remaining,
                "quota_limit":      10000,
                "percent_used":     round(pct, 2),
            })
        else:
            quota_col  = clr.G if pct < 50 else (clr.Y if pct < 80 else clr.R)
            status_lbl = "Good" if pct < 50 else ("Moderate" if pct < 80 else "High")
            print()
            print(f"  {clr.C}{LINE}{clr.RST}")
            print(f"  {clr.C}  YouTube API Quota{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}Today's Usage{clr.RST}")
            print(f"  {clr.C}{LINE}{clr.RST}")
            print()
            print(f"  {clr.W}  Used       {clr.DIM}:{clr.RST}  {quota_col}{used:,} units{clr.RST}  {clr.DIM}({pct:.1f}%){clr.RST}")
            print(f"  {clr.W}  Remaining  {clr.DIM}:{clr.RST}  {clr.G}{remaining:,} units{clr.RST}")
            print(f"  {clr.W}  Daily Limit{clr.DIM}:{clr.RST}  {clr.W}10,000 units{clr.RST}")
            print(f"  {clr.W}  Status     {clr.DIM}:{clr.RST}  {quota_col}{status_lbl}{clr.RST}")
            print()
            print(f"  {clr.DIM}Note: This tracks Aevum's usage only. Quota resets daily at midnight PT.{clr.RST}")
            print()
        sys.exit(EX.OK)

    # ── cache ─────────────────────────────────────────────────────────
    if cmd == 'cache':
        cmd_cache(args)
        sys.exit(EX.OK)

    # ── watch ─────────────────────────────────────────────────────────
    if cmd == 'watch':
        import time as _time
        folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("watch", use_json)

        interval  = max(1.0, args.interval)
        no_clear  = args.no_clear or use_json
        sort      = _resolve_sort(args, cfg)
        top       = _resolve_top(args, cfg)
        filters   = _build_filters(args, use_json)
        uc        = _use_cache(args, cfg)  # Issue 33

        def _folder_snapshot(root):
            snap = {}
            try:
                snap[str(root)] = root.stat().st_mtime
                for entry in os.scandir(root):
                    if entry.is_dir(follow_symlinks=False):
                        snap[entry.path] = entry.stat().st_mtime
            except OSError:
                pass
            return snap

        def _do_scan():
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, None, sort, use_cache=uc)
            if filters:
                durations, sizes = apply_filters(durations, sizes, filters)
                total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                    folder, durations, sizes, sort)
            return total_sec, total_count, tree, durations, sizes, hits

        if not quiet:
            print(f"\n  {clr.C}Watching{clr.RST}  {clr.W}{folder}{clr.RST}  "
                  f"{clr.DIM}(interval: {interval}s — Ctrl+C to stop){clr.RST}\n")

        update_n  = 0
        last_snap = {}
        last_sec  = None

        while True:
            try:
                snap    = _folder_snapshot(folder)
                changed = snap != last_snap

                if changed or update_n == 0:
                    last_snap = snap
                    try:
                        total_sec, total_count, tree, durations, sizes, hits = _do_scan()
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        if use_json:
                            print(json.dumps({"status": "error", "error": str(e),
                                              "timestamp": datetime.now().isoformat()}), flush=True)
                        else:
                            print(f"  {clr.R}[ERROR]{clr.RST} Scan failed: {e}", file=sys.stderr)
                        _time.sleep(interval)
                        continue

                    update_n += 1
                    ts = datetime.now().strftime("%H:%M:%S")

                    if use_json:
                        payload = _scan_to_json(folder, total_sec, total_count,
                                                tree, durations, sizes, hits)
                        payload["watch_update"]    = update_n
                        payload["timestamp"]       = datetime.now().isoformat()
                        payload["changed"]         = changed
                        payload["total_sec_delta"] = round(total_sec - (last_sec or total_sec), 2)
                        print(json.dumps(payload, ensure_ascii=False), flush=True)
                    else:
                        if not no_clear:
                            clear()
                        # Issue 14 fix: use clr.G / clr.R instead of bare G / R
                        delta_str = ""
                        if last_sec is not None and last_sec != total_sec:
                            delta    = total_sec - last_sec
                            sign     = "+" if delta >= 0 else ""
                            dfmt     = format_duration(abs(delta))["hours_fmt"]
                            dcol     = clr.G if delta >= 0 else clr.R   # Issue 14
                            delta_str = f"  {dcol}{sign}{dfmt}{clr.RST}"
                        print(f"  {clr.C}{LINE}{clr.RST}")
                        print(f"  {clr.C}  Watching{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}{folder.name}{clr.RST}  "
                              f"{clr.DIM}|{clr.RST}  {clr.G}#{update_n}{clr.RST}  "
                              f"{clr.DIM}@ {ts}{clr.RST}{delta_str}")
                        print(f"  {clr.C}{LINE}{clr.RST}")
                        print()
                        # Issue 26: forward depth (None → default 50 inside print_results)
                        print_results(folder, total_sec, total_count, tree,
                                      durations, sizes, top, show_files=False,
                                      max_depth=getattr(args, 'depth', None) or 50,
                                      speeds=getattr(args, 'speeds', None) or None)
                        print(f"  {clr.DIM}Next check in {interval}s — Ctrl+C to stop{clr.RST}\n")
                    last_sec = total_sec

                _time.sleep(interval)

            except KeyboardInterrupt:
                if use_json:
                    print(json.dumps({"status": "stopped", "updates": update_n,
                                      "timestamp": datetime.now().isoformat()}), flush=True)
                else:
                    print(f"\n\n  {clr.G}Watch stopped.{clr.RST}  {clr.DIM}{update_n} update(s) shown.{clr.RST}\n")
                sys.exit(EX.OK)

    # ── compare ───────────────────────────────────────────────────────
    if cmd == 'compare':
        folder_a = Path(_resolve_alias(args.folder_a.strip().strip("'\""), cfg))
        folder_b = Path(_resolve_alias(args.folder_b.strip().strip("'\""), cfg))
        for f in (folder_a, folder_b):
            if not f.exists() or not f.is_dir():
                if use_json:
                    _json_error(f"Not a valid folder: {f}", EX.ERR_ARGS)
                print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {f}\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
        _require_ffprobe("compare", use_json)
        sort    = _resolve_sort(args, cfg)
        on_prog = _make_progress_bar(quiet, use_json)
        uc      = _use_cache(args, cfg)   # Issue 33
        try:
            data_a, data_b = run_compare(folder_a, folder_b, on_prog, sort, uc)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Cancelled.{clr.RST}\n")
            sys.exit(EX.ERR_SCAN)
        if use_json:
            _json_out(_compare_to_json(folder_a, folder_b, data_a, data_b))
        else:
            print_comparison(folder_a, folder_b, data_a, data_b)
        sys.exit(EX.OK)

    # ── dupes ─────────────────────────────────────────────────────────
    if cmd == 'dupes':
        folder = Path(_resolve_alias(args.folder.strip().strip("'\""), cfg))
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("dupes", use_json)
        on_prog = _make_progress_bar(quiet, use_json)
        uc      = _use_cache(args, cfg)   # Issue 33
        if not quiet:
            print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
        try:
            _, _, _, durations, sizes, hits = _run_scan(folder, on_prog, "name", uc)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
            sys.exit(EX.ERR_SCAN)
        if not quiet:
            probed     = len(durations) - hits
            cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
            print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{len(durations)}{clr.RST} files found.{cache_info}".ljust(100))
        groups = find_duplicates(durations, sizes)
        if use_json:
            _json_out(_dupes_to_json(groups, durations, sizes))
            sys.exit(EX.OK)
        print_duplicates(groups, durations)
        if args.out:
            import io
            buf = io.StringIO()
            if not groups:
                buf.write("No duplicates found.\n")
            else:
                for i, group in enumerate(groups, 1):
                    from ._dupes import _group_wasted
                    median_sec, _ = _group_wasted(group, durations)
                    buf.write(f"Group {i}  |  {format_duration(median_sec)['hours_fmt']}  |  {len(group)} copies\n")
                    for p in group:
                        buf.write(f"  -> {p}\n")
                    buf.write("\n")
            try:
                Path(args.out).write_text(buf.getvalue(), encoding="utf-8")
                if not quiet:
                    print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{args.out}{clr.RST}\n")
            except Exception as e:
                print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_EXPORT)
        sys.exit(EX.OK)

    # ── export ────────────────────────────────────────────────────────
    if cmd == 'export':
        raw      = _resolve_alias(args.target.strip().strip("'\""), cfg)
        sort     = _resolve_sort(args, cfg)
        uc       = _use_cache(args, cfg)   # Issue 33
        out_path = args.out or None
        fmt      = args.format

        if _is_url(raw):
            url_prog = None if (quiet or use_json) else _make_progress_bar(quiet, use_json)
            try:
                total_sec, total_count, entries, label, cache_hits, unavailable_count = \
                    scan_url(raw, url_prog, use_cache=uc)
            except KeyboardInterrupt:
                if use_json:
                    _json_error("Fetch interrupted", EX.ERR_SCAN)
                print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n")
                sys.exit(EX.ERR_SCAN)
            except Exception as e:
                if use_json:
                    _json_error(str(e), EX.ERR_API)
                print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_API)
            if not quiet:
                api_fetched  = total_count - cache_hits
                yt_info      = (f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}"
                                if api_fetched > 0 else
                                f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}")
                unavail_note = f"  {clr.Y}({unavailable_count} unavailable){clr.RST}" if unavailable_count > 0 else ""
                print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}{unavail_note}".ljust(100))
            # Issue 30 fix: delegate to export_url_results() which supports all three formats
            try:
                dest = export_url_results(raw, label, total_sec, total_count, entries, fmt, out_path)
                if not quiet:
                    print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{dest}{clr.RST}\n")
            except Exception as e:
                if use_json:
                    _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
                print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
                sys.exit(EX.ERR_EXPORT)
            sys.exit(EX.OK)

        folder = Path(raw)
        if not folder.exists() or not folder.is_dir():
            if use_json:
                _json_error(f"Not a valid folder: {folder}", EX.ERR_ARGS)
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("export", use_json)
        on_prog = _make_progress_bar(quiet, use_json)
        if not quiet:
            print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_prog, sort, uc)
        except KeyboardInterrupt:
            if use_json:
                _json_error("Scan interrupted", EX.ERR_SCAN)
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
            sys.exit(EX.ERR_SCAN)
        if not quiet:
            print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.".ljust(100))
        try:
            dest = export_results(folder, total_sec, total_count, tree, durations, fmt, out_path)
            if not quiet:
                print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{dest}{clr.RST}\n")
        except Exception as e:
            if use_json:
                _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
            print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
            sys.exit(EX.ERR_EXPORT)
        sys.exit(EX.OK)

    # ── files ─────────────────────────────────────────────────────────
    if cmd == 'files':
        folder_raw = _resolve_alias(args.folder.strip().strip("'\""), cfg)
        folder     = Path(folder_raw)
        if not folder.exists() or not folder.is_dir():
            print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {folder}\n", file=sys.stderr)
            sys.exit(EX.ERR_ARGS)
        _require_ffprobe("files", use_json)
        sort    = _resolve_sort(args, cfg)
        top     = _resolve_top(args, cfg)
        uc      = _use_cache(args, cfg)   # Issue 33
        on_prog = _make_progress_bar(quiet, use_json)
        if not quiet:
            print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
        try:
            total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                folder, on_prog, sort, uc)
        except KeyboardInterrupt:
            print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
            sys.exit(EX.ERR_SCAN)
        if not quiet:
            probed     = total_count - hits
            cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
            print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.{cache_info}".ljust(100))
        print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                      show_files=True, max_depth=getattr(args, 'depth', None) or 50,
                      speeds=getattr(args, 'speeds', None) or None)
        sys.exit(EX.OK)

    # ── scan ──────────────────────────────────────────────────────────
    if cmd == 'scan' and getattr(args, 'targets', None) is not None:
        targets = [t.strip().strip("'\"") for t in args.targets]

        if not targets:
            print(f"\n  {clr.R}[ERROR]{clr.RST} No target specified. Usage: aevum scan <path|url>\n",
                  file=sys.stderr)
            sys.exit(EX.ERR_ARGS)

        # Collapse multi-token unquoted paths (spaces in path name).
        # Issue 20 fix: only collapse when the remaining tokens don't look like
        # independent paths — if they do, treat them as a real batch scan.
        if len(targets) > 1 and not any(_is_url(t) for t in targets):
            first = targets[0]
            first_looks_like_path = ':' in first or first.startswith(('/', '\\', '.'))
            rest_look_like_paths  = any(
                ':' in t or t.startswith(('/', '\\')) for t in targets[1:]
            )
            if first_looks_like_path and not rest_look_like_paths:
                targets = [' '.join(targets)]

        sort     = _resolve_sort(args, cfg)
        top      = _resolve_top(args, cfg)
        uc       = _use_cache(args, cfg)   # Issue 33
        out_path = getattr(args, 'out', None)
        fmt      = _resolve_out_format(out_path, getattr(args, 'fmt', None))
        do_merge = getattr(args, 'merge', False)
        max_d        = getattr(args, 'depth', None) or 50
        custom_speeds = getattr(args, 'speeds', None) or None

        if len(targets) == 1:
            # ── single target ─────────────────────────────────────────
            raw     = _resolve_alias(targets[0], cfg)
            filters = _build_filters(args, use_json)

            if _is_url(raw):
                url_prog = None if (quiet or use_json) else _make_progress_bar(quiet, use_json)
                try:
                    total_sec, total_count, entries, label, cache_hits, unavailable_count = \
                        scan_url(raw, url_prog, use_cache=uc)
                except KeyboardInterrupt:
                    if use_json:
                        _json_error("Fetch interrupted", EX.ERR_SCAN)
                    print(f"\n\n  {clr.Y}Fetch cancelled.{clr.RST}\n")
                    sys.exit(EX.ERR_SCAN)
                except Exception as e:
                    if use_json:
                        _json_error(str(e), EX.ERR_API)
                    print(f"\n  {clr.R}[ERROR]{clr.RST} {e}\n", file=sys.stderr)
                    sys.exit(EX.ERR_API)
                if use_json:
                    _json_out(_url_to_json(raw, label, total_sec, total_count, entries))
                else:
                    if not quiet:
                        api_fetched  = total_count - cache_hits
                        yt_info      = (f"  {clr.W}({cache_hits} cached, {api_fetched} fetched via API){clr.RST}"
                                        if api_fetched > 0 else
                                        f"  {clr.W}({cache_hits} cached, 0 API calls){clr.RST}")
                        unavail_note = f"  {clr.Y}({unavailable_count} unavailable){clr.RST}" if unavailable_count > 0 else ""
                        print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count} videos found.{clr.RST}{yt_info}{unavail_note}".ljust(100))
                    print_url_results(raw, label, total_sec, total_count, entries,
                                      top_n=top, unavailable_count=unavailable_count,
                                      speeds=custom_speeds)
                sys.exit(EX.OK)

            folder = Path(raw)
            if not folder.exists():
                if use_json:
                    _json_error(f"Path not found: {folder}", EX.ERR_ARGS)
                print(f"\n  {clr.R}[ERROR]{clr.RST} Path not found: {folder}", file=sys.stderr)
                try:
                    sug = _fuzzy_suggest(folder.name,
                                         [p.name for p in folder.parent.iterdir() if p.is_dir()])
                    if sug:
                        print(f"  {clr.DIM}Did you mean:{clr.RST}  {clr.W}{folder.parent / sug}{clr.RST}", file=sys.stderr)
                except Exception:
                    pass
                print()
                sys.exit(EX.ERR_ARGS)
            if not folder.is_dir():
                if use_json:
                    _json_error(f"That is a file, not a folder: {folder}", EX.ERR_ARGS)
                print(f"\n  {clr.R}[ERROR]{clr.RST} That is a file, not a folder: {folder}\n", file=sys.stderr)
                sys.exit(EX.ERR_ARGS)
            _require_ffprobe("scan", use_json)

            on_progress = _make_progress_bar(quiet, use_json)
            if not quiet:
                print(f"  {clr.DIM}Collecting files...{clr.RST}", end='', flush=True)
            try:
                total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                    folder, on_progress, sort, uc)
            except KeyboardInterrupt:
                if use_json:
                    _json_error("Scan interrupted", EX.ERR_SCAN)
                print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
                sys.exit(EX.ERR_SCAN)

            if filters:
                durations, sizes = apply_filters(durations, sizes, filters)
                total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                    folder, durations, sizes, sort)

            if use_json:
                _json_out(_scan_to_json(folder, total_sec, total_count, tree, durations, sizes, hits))
                sys.exit(EX.OK)

            if not quiet:
                probed     = total_count - hits
                cache_info = f"  {clr.DIM}({hits} cached, {probed} probed){clr.RST}" if hits > 0 else ""
                print(f"\r  {clr.G}Done!{clr.RST}  {clr.W}{total_count}{clr.RST} files found.{cache_info}".ljust(100))

            # Issue 26: forward max_depth
            print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                          show_files=getattr(args, 'files', False), max_depth=max_d,
                          speeds=custom_speeds)
            groups = find_duplicates(durations, sizes)
            if not quiet:
                print_dupe_warning(groups, folder)
            if fmt and out_path:
                try:
                    dest = export_results(folder, total_sec, total_count, tree,
                                          durations, fmt, out_path)
                    if not quiet:
                        print(f"  {clr.G}Exported{clr.RST}  {clr.DIM}→{clr.RST}  {clr.W}{dest}{clr.RST}\n")
                except Exception as e:
                    if use_json:
                        _json_error(f"Export failed: {e}", EX.ERR_EXPORT)
                    print(f"  {clr.R}Export failed:{clr.RST} {e}\n", file=sys.stderr)
                    sys.exit(EX.ERR_EXPORT)
            sys.exit(EX.OK)

        else:
            # ── multi-target batch ────────────────────────────────────
            _require_ffprobe("scan", use_json)
            filters = _build_filters(args, use_json)

            # Issue 19 fix: resolve aliases for every target in the batch loop
            folders = []
            for raw in [_resolve_alias(t, cfg) for t in targets]:   # Issue 19
                if _is_url(raw):
                    if use_json:
                        _json_error("Batch mode does not support URLs — use a single URL target", EX.ERR_ARGS)
                    print(f"\n  {clr.R}[ERROR]{clr.RST} Batch mode does not support URLs: {raw}\n", file=sys.stderr)
                    sys.exit(EX.ERR_ARGS)
                f = Path(raw)
                if not f.exists() or not f.is_dir():
                    if use_json:
                        _json_error(f"Not a valid folder: {f}", EX.ERR_ARGS)
                    print(f"\n  {clr.R}[ERROR]{clr.RST} Not a valid folder: {f}\n", file=sys.stderr)
                    sys.exit(EX.ERR_ARGS)
                folders.append(f)

            results = []
            for i, folder in enumerate(folders, 1):
                if not quiet:
                    label_w = 40
                    print(f"  {clr.DIM}[{i}/{len(folders)}]{clr.RST}  {clr.W}{folder.name:<{label_w}}{clr.RST}  "
                          f"{clr.DIM}scanning...{clr.RST}", end='', flush=True)
                on_progress = _make_progress_bar(quiet=True)
                try:
                    total_sec, total_count, tree, durations, sizes, hits = _run_scan(
                        folder, on_progress, sort, uc)
                except KeyboardInterrupt:
                    if use_json:
                        _json_error("Scan interrupted", EX.ERR_SCAN)
                    print(f"\n\n  {clr.Y}Scan cancelled.{clr.RST}\n")
                    sys.exit(EX.ERR_SCAN)
                if filters:
                    durations, sizes = apply_filters(durations, sizes, filters)
                    total_sec, total_count, tree, durations, sizes = rebuild_after_filter(
                        folder, durations, sizes, sort)
                results.append((folder, total_sec, total_count, tree, durations, sizes, hits))
                if not quiet:
                    fmt_dur = format_duration(total_sec)["hours_fmt"]
                    print(f"\r  {clr.G}[{i}/{len(folders)}]{clr.RST}  {clr.W}{folder.name:<{label_w}}{clr.RST}  "
                          f"{clr.Y}{fmt_dur}{clr.RST}  {clr.DIM}{total_count} files{clr.RST}".ljust(100))

            if do_merge:
                merged_sec   = sum(r[1] for r in results)
                merged_count = sum(r[2] for r in results)
                merged_dur   = {}
                merged_sizes = {}
                merged_hits  = sum(r[6] for r in results)
                for _, _, _, _, dur, sz, _ in results:
                    merged_dur.update(dur)
                    merged_sizes.update(sz)

                if use_json:
                    _json_out({
                        "status":      "ok",
                        "command":     "scan",
                        "mode":        "batch_merged",
                        "paths":       [str(f.resolve()) for f in folders],
                        "total_files": merged_count,
                        "total_bytes": sum(merged_sizes.values()),
                        "total_sec":   round(merged_sec, 2),
                        "duration":    format_duration(merged_sec),
                        "cache_hits":  merged_hits,
                        "per_folder": [
                            {
                                "path":        str(r[0].resolve()),
                                "name":        r[0].name,
                                "total_files": r[2],
                                "total_sec":   round(r[1], 2),
                                "duration":    format_duration(r[1])["hours_fmt"],
                            }
                            for r in results
                        ],
                        "files": [
                            {
                                "path":     str(p),
                                "filename": p.name,
                                "folder":   p.parent.name,
                                "seconds":  round(s, 2),
                                "duration": format_duration(s)["hours_fmt"],
                            }
                            for p, s in sorted(merged_dur.items(), key=lambda x: x[1], reverse=True)
                        ],
                    })
                    sys.exit(EX.OK)

                fmt_merged = format_duration(merged_sec)
                print()
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Batch Scan  {clr.DIM}|{clr.RST}  {len(folders)} folders  {clr.DIM}(merged){clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                for r in results:
                    fd = format_duration(r[1])["hours_fmt"]
                    print(f"  {clr.DIM}→{clr.RST}  {clr.W}{r[0].name:<35}{clr.RST}  {clr.Y}{fd}{clr.RST}  {clr.DIM}{r[2]} files{clr.RST}")
                print()
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Grand Total{clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                total_bytes = sum(merged_sizes.values())
                print(f"  {clr.W}  Total files   {clr.DIM}:{clr.RST}  {clr.W}{merged_count}{clr.RST}")
                print(f"  {clr.W}  Total size    {clr.DIM}:{clr.RST}  {clr.W}{format_size(total_bytes)}{clr.RST}")
                print(f"  {clr.W}  Days          {clr.DIM}:{clr.RST}  {clr.W}{fmt_merged['days_fmt']}{clr.RST}")
                print(f"  {clr.W}  Hours         {clr.DIM}:{clr.RST}  {clr.W}{fmt_merged['hours_fmt']}{clr.RST}")
                print()
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Playback Speed{clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                _extras = [s for s in (custom_speeds or []) if s not in (1.0, 1.25, 1.5, 1.75, 2.0)]
                for speed in [1.0, 1.25, 1.5, 1.75, 2.0]:
                    adj  = format_duration(merged_sec / speed)
                    slbl = f"{speed:.6g}x"
                    print(f"  {clr.W}  {slbl:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adj['hours_fmt']}{clr.RST}  {clr.DIM}({adj['days_fmt']}){clr.RST}")
                if _extras:
                    print(f"  {clr.DIM}  {chr(9472) * 40}{clr.RST}")
                    for speed in _extras:
                        adj  = format_duration(merged_sec / speed)
                        slbl = f"{speed:.6g}x"
                        print(f"  {clr.W}  {slbl:<10}     {clr.DIM}:{clr.RST}  {clr.W}{adj['hours_fmt']}{clr.RST}  {clr.DIM}({adj['days_fmt']}){clr.RST}")
                print()
                if top > 0:
                    from ._display import print_top_files
                    print_top_files(merged_dur, top)

            else:
                if use_json:
                    _json_out({
                        "status":  "ok",
                        "command": "scan",
                        "mode":    "batch",
                        "results": [
                            _scan_to_json(r[0], r[1], r[2], r[3], r[4], r[5], r[6])
                            for r in results
                        ],
                    })
                    sys.exit(EX.OK)

                for folder, total_sec, total_count, tree, durations, sizes, hits in results:
                    # Issue 26: forward max_depth
                    print_results(folder, total_sec, total_count, tree, durations, sizes, top,
                                  show_files=getattr(args, 'files', False), max_depth=max_d,
                                  speeds=custom_speeds)
                    groups = find_duplicates(durations, sizes)
                    if not quiet:
                        print_dupe_warning(groups, folder)

                batch_total_sec   = sum(r[1] for r in results)
                batch_total_count = sum(r[2] for r in results)
                batch_total_bytes = sum(sum(r[5].values()) for r in results)
                print(f"  {clr.C}{LINE}{clr.RST}")
                print(f"  {clr.W}  Batch Summary  {clr.DIM}|{clr.RST}  {len(folders)} folders{clr.RST}")
                print(f"  {clr.C}{LINE}{clr.RST}")
                for r in results:
                    fd = format_duration(r[1])["hours_fmt"]
                    print(f"  {clr.DIM}→{clr.RST}  {clr.W}{r[0].name:<35}{clr.RST}  {clr.Y}{fd}{clr.RST}  {clr.DIM}{r[2]} files{clr.RST}")
                print()
                bfmt = format_duration(batch_total_sec)
                print(f"  {clr.W}  Combined  {clr.DIM}:{clr.RST}  {clr.W}{bfmt['hours_fmt']}{clr.RST}  "
                      f"{clr.DIM}|{clr.RST}  {clr.W}{batch_total_count} files{clr.RST}  "
                      f"{clr.DIM}|{clr.RST}  {clr.W}{format_size(batch_total_bytes)}{clr.RST}")
                print()

            sys.exit(EX.OK)
