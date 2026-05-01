import json
import os
import subprocess
import sys
import types
from pathlib import Path

from ._color   import clr, LINE
from ._paths   import CACHE_DIR, CONFIG_FILE          # Issue 23: central path
from ._cache   import _cache_key
from ._scan    import format_size, check_ffprobe
from ._apikey  import load_api_key, save_api_key, get_storage_method
from ._youtube import prompt_api_key, yt_cache_stats, yt_cache_clear

CONFIG_DEFAULTS = {
    "sort":          "name:asc",
    "top":           10,
    "no_color":      False,
    "cache_enabled": True,
    "export_dir":    "",
    "aliases":       {},
    "project_dir":   "",
}


def load_config():
    """
    Load configuration from disk with validation.
    
    Security: Validates JSON structure and types to prevent deserialization
    attacks. Only accepts expected configuration keys and types.
    """
    try:
        with CONFIG_FILE.open('r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Security: validate root is a dict
        if not isinstance(data, dict):
            return dict(CONFIG_DEFAULTS)
        
        # Security: validate each field type matches defaults
        validated = {}
        for key, default_value in CONFIG_DEFAULTS.items():
            if key in data:
                value = data[key]
                expected_type = type(default_value)
                
                # Validate type matches
                if isinstance(value, expected_type):
                    # Additional validation for specific keys
                    if key == "top":
                        # Limit to reasonable range
                        if 0 <= value <= 100:
                            validated[key] = value
                    elif key == "sort":
                        # Validate sort field
                        valid_sorts = [
                            "name:asc", "name:desc",
                            "duration:asc", "duration:desc",
                            "count:asc", "count:desc"
                        ]
                        if value in valid_sorts:
                            validated[key] = value
                    elif key == "aliases":
                        # Validate aliases is a dict of str -> str
                        if isinstance(value, dict):
                            clean_aliases = {}
                            for k, v in value.items():
                                if isinstance(k, str) and isinstance(v, str):
                                    # Limit alias name length
                                    if len(k) <= 50 and len(v) <= 4096:
                                        clean_aliases[k] = v
                            validated[key] = clean_aliases
                    else:
                        validated[key] = value
        
        return {**CONFIG_DEFAULTS, **validated}
    except (json.JSONDecodeError, OSError):
        return dict(CONFIG_DEFAULTS)


def save_config(cfg):
    """
    Persist config to disk.

    Issue 25 note: write failures are printed to stderr and propagated back
    to the caller as False so the caller can decide whether to warn the user
    more prominently (e.g. the interactive REPL can print a second reminder).
    Previously the error was printed but the function always returned None,
    giving the caller no way to react.
    """
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"  {clr.R}Config write failed:{clr.RST} {e}", file=sys.stderr)
        return False


def _config_key_valid(key):
    return key in CONFIG_DEFAULTS


def cmd_doctor(cfg):
    print()
    print(f"  {clr.C}{LINE}{clr.RST}")
    print(f"  {clr.C}  Aevum Doctor{clr.RST}  {clr.DIM}|{clr.RST}  {clr.W}Environment Check{clr.RST}")
    print(f"  {clr.C}{LINE}{clr.RST}")
    print()

    pv = sys.version.split()[0]
    print(f"  {clr.G}[OK]{clr.RST}   Python {pv}")

    try:
        r  = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True)
        fv = r.stdout.splitlines()[0] if r.stdout else "unknown"
        print(f"  {clr.G}[OK]{clr.RST}   {fv}")
    except FileNotFoundError:
        print(f"  {clr.R}[FAIL]{clr.RST}  ffprobe not found on PATH")
        print(f"         Install FFmpeg: {clr.C}https://ffmpeg.org/download.html{clr.RST}")

    api_key = load_api_key()
    if api_key:
        storage = get_storage_method()
        storage_name = {
            "keyring": "system keyring (encrypted)",
            "encrypted_file": "encrypted file", 
            "plaintext_file": "plaintext file",
        }.get(storage, "secure storage")
        print(f"  {clr.G}[OK]{clr.RST}   YouTube API key set  {clr.DIM}(stored in {storage_name}){clr.RST}")
        from ._youtube import get_quota_status
        used, remaining, pct = get_quota_status()
        quota_col = clr.G if pct < 50 else (clr.Y if pct < 80 else clr.R)
        print(
            f"  {clr.G}[OK]{clr.RST}   YouTube quota: "
            f"{quota_col}{used:,}/10,000 units used{clr.RST}  "
            f"{clr.DIM}({remaining:,} remaining, {pct:.1f}%){clr.RST}"
        )
    else:
        print(f"  {clr.Y}[WARN]{clr.RST}  YouTube API key not set")
        print(f"         Set it with: {clr.W}aevum config set yt_api_key <key>{clr.RST}")

    try:
        files       = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
        total_bytes = sum(f.stat().st_size for f in files)
        print(f"  {clr.G}[OK]{clr.RST}   Cache: {len(files)} entries, {format_size(total_bytes)} at {CACHE_DIR}")
    except Exception:
        print(f"  {clr.Y}[WARN]{clr.RST}  Could not read cache directory: {CACHE_DIR}")

    if CONFIG_FILE.exists():
        print(f"  {clr.G}[OK]{clr.RST}   Config: {CONFIG_FILE}")
    else:
        print(f"  {clr.DIM}[INFO]{clr.RST}  No config file (using defaults). {clr.DIM}{CONFIG_FILE}{clr.RST}")
    print()


def cmd_cache(args):
    action = args.action or "list"

    if action == "path":
        print(f"  {clr.W}{CACHE_DIR}{clr.RST}")
        return

    if action == "list":
        if not CACHE_DIR.exists() or not list(CACHE_DIR.glob("*.json")):
            print(f"  {clr.DIM}Cache is empty.{clr.RST}  {clr.W}{CACHE_DIR}{clr.RST}")
            return
        files = sorted(CACHE_DIR.glob("*.json"))
        print()
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.W}  Cache  {clr.DIM}|{clr.RST}  {CACHE_DIR}{clr.RST}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        print()
        total = 0
        for f in files:
            sz    = f.stat().st_size
            total += sz
            try:
                data        = json.loads(f.read_text(encoding="utf-8"))
                folder_path = data[0]["path"].rsplit(os.sep, 1)[0] if data else "?"
                count       = len(data)
            except Exception:
                folder_path = "?"
                count       = 0
            print(
                f"  {clr.DIM}{f.name[:16]}{clr.RST}  "
                f"{clr.W}{folder_path}{clr.RST}  "
                f"{clr.DIM}({count} files, {format_size(sz)}){clr.RST}"
            )
        yt_count, yt_size = yt_cache_stats()
        if yt_count:
            print(
                f"  {clr.DIM}yt_video_cache  {clr.RST}  "
                f"{clr.W}YouTube videos{clr.RST}  "
                f"{clr.DIM}({yt_count} videos, {format_size(yt_size)}){clr.RST}"
            )
            total += yt_size
        yt_note = f" + YouTube cache" if yt_count else ""
        print()
        print(f"  {clr.DIM}Total: {len(files)} local cache files{yt_note}, {format_size(total)}{clr.RST}")
        print()
        return

    if action == "clear":
        target_folder = getattr(args, "folder", None)
        if target_folder:
            key = _cache_key(target_folder)
            if key.exists():
                key.unlink()
                print(f"  {clr.G}[OK]{clr.RST}  Cleared cache for {target_folder}")
            else:
                print(f"  {clr.DIM}[SKIP]{clr.RST}  No cache found for {target_folder}")
        else:
            if not CACHE_DIR.exists():
                print(f"  {clr.DIM}Cache is already empty.{clr.RST}")
                return
            files      = list(CACHE_DIR.glob("*.json"))
            for f in files:
                f.unlink()
            yt_cleared = yt_cache_clear()
            yt_note    = "  +  YouTube video cache" if yt_cleared else ""
            print(f"  {clr.G}[OK]{clr.RST}  Cleared {len(files)} local cache files from {CACHE_DIR}{yt_note}")


def cmd_config(args, cfg):
    action = args.action
    YT_KEY = "yt_api_key"

    if action == "list":
        print()
        print(f"  {clr.C}{LINE}{clr.RST}")
        print(f"  {clr.W}  Configuration{clr.RST}  {clr.DIM}|{clr.RST}  {CONFIG_FILE}")
        print(f"  {clr.C}{LINE}{clr.RST}")
        print()
        for k, v in cfg.items():
            print(f"  {clr.G}{k:<18}{clr.RST}  {clr.W}{v}{clr.RST}")
        api_key = load_api_key()
        if api_key:
            storage = get_storage_method()
            status = f"(set - stored in {storage})"
        else:
            status = "(not set)"
        print(f"  {clr.G}{YT_KEY:<18}{clr.RST}  {clr.W}{status}{clr.RST}")
        print()
        return

    if action == "reset":
        save_config(dict(CONFIG_DEFAULTS))
        print(f"  {clr.G}[OK]{clr.RST}  Configuration reset to defaults.")
        return

    key = args.key
    if not key:
        print(f"  {clr.R}[ERROR]{clr.RST} Key required. Run 'aevum config list' to see all keys.", file=sys.stderr)
        sys.exit(1)

    if action == "get":
        if key == YT_KEY:
            api_key = load_api_key()
            if api_key:
                storage = get_storage_method()
                print(f"API key is set (stored in {storage})")
                print(f"Use 'aevum doctor' to verify it works")
            else:
                print("(not set)")
        elif _config_key_valid(key):
            print(cfg.get(key, CONFIG_DEFAULTS.get(key)))
        else:
            print(f"  {clr.R}[ERROR]{clr.RST} Unknown key: {key}", file=sys.stderr)
            sys.exit(1)
        return

    if action == "set":
        value = args.value
        if key == YT_KEY:
            if not value:
                prompt_api_key()
                return
            if save_api_key(value):
                storage = get_storage_method()
                print(f"  {clr.G}[OK]{clr.RST}  yt_api_key saved to {storage}.")
            else:
                print(f"  {clr.R}[ERROR]{clr.RST}  Failed to save API key.", file=sys.stderr)
            return
        if not _config_key_valid(key):
            print(
                f"  {clr.R}[ERROR]{clr.RST} Unknown key: {key}. "
                f"Run 'aevum config list' to see all keys.",
                file=sys.stderr,
            )
            sys.exit(1)
        if value is None:
            print(
                f"  {clr.R}[ERROR]{clr.RST} Value required: aevum config set {key} <value>",
                file=sys.stderr,
            )
            sys.exit(1)
        default = CONFIG_DEFAULTS[key]
        try:
            if isinstance(default, bool):
                _true  = {"1", "true", "yes", "on"}
                _false = {"0", "false", "no", "off"}
                if value.lower() in _true:
                    coerced = True
                elif value.lower() in _false:
                    coerced = False
                else:
                    print(
                        f"  {clr.R}[ERROR]{clr.RST} Invalid value for {key}: '{value}'. "
                        f"Use true/false, yes/no, on/off, or 1/0.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            elif isinstance(default, int):
                coerced = int(value)
            else:
                coerced = value
        except (ValueError, AttributeError):
            print(f"  {clr.R}[ERROR]{clr.RST} Invalid value for {key}: {value}", file=sys.stderr)
            sys.exit(1)
        cfg[key] = coerced
        ok = save_config(cfg)
        if ok:
            print(f"  {clr.G}[OK]{clr.RST}  {key} = {coerced}")
        else:
            # Issue 25: surface write failure more prominently
            print(
                f"  {clr.Y}[WARN]{clr.RST}  Setting applied for this session but "
                f"could not be saved to disk.",
                file=sys.stderr,
            )


def repl_config(parts, cfg):
    """
    Handle 'config ...' typed inside the interactive REPL.

    Issue 24 fix: values with spaces (e.g. export_dir paths) are now
    preserved by joining everything from parts[2] onward instead of only
    taking parts[2].
    """
    if not parts:
        print(
            f"  {clr.DIM}Usage: config get <key> | config set <key> <value> | "
            f"config list | config reset{clr.RST}\n"
        )
        return
    ns = types.SimpleNamespace(
        action=parts[0]               if parts         else "list",
        key   =parts[1]               if len(parts) > 1 else None,
        # Issue 24: join remaining tokens so paths with spaces work
        value =" ".join(parts[2:])    if len(parts) > 2 else None,
        no_color=False,
    )
    cmd_config(ns, cfg)
