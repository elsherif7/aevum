import json
import os
import sys
from pathlib import Path

from ._color   import clr, LINE
from ._paths import CACHE_DIR, CONFIG_FILE
from ._scan    import format_size
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
    
    Q-04 fix: warns when a config value is rejected so users can debug
    config issues instead of silently getting defaults.
    B-06 fix: normalizes bare sort names (e.g. "duration" -> "duration:desc").
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
                        else:
                            print(f"  [CONFIG] Warning: 'top' value {value!r} out of range (0-100), using default.", file=sys.stderr)
                    elif key == "sort":
                        # B-06: normalize bare sort names (e.g. "duration" → "duration:desc")
                        # to be consistent with _resolve_sort() in _cli.py
                        if ':' not in value:
                            _sort_defaults = {'name': 'asc', 'duration': 'desc', 'count': 'desc'}
                            value = value + ':' + _sort_defaults.get(value, 'asc')
                        valid_sorts = [
                            "name:asc", "name:desc",
                            "duration:asc", "duration:desc",
                            "count:asc", "count:desc"
                        ]
                        if value in valid_sorts:
                            validated[key] = value
                        else:
                            print(f"  [CONFIG] Warning: 'sort' value {value!r} is not valid, using default.", file=sys.stderr)
                    elif key == "aliases":
                        # Validate aliases is a dict of str -> str
                        if isinstance(value, dict):
                            clean_aliases = {}
                            for k, v in value.items():
                                if isinstance(k, str) and isinstance(v, str):
                                    # Limit lengths only
                                    if len(k) <= 50 and len(v) <= 4096:
                                        clean_aliases[k] = v
                                    else:
                                        print(f"  [CONFIG] Warning: alias '{k}' exceeds length limit, skipped.", file=sys.stderr)
                            validated[key] = clean_aliases
                    elif key == "project_dir":
                        # Must be empty or an absolute path
                        if not value or Path(value).is_absolute():
                            validated[key] = value
                        else:
                            print(f"  [CONFIG] Warning: 'project_dir' must be an absolute path, using default.", file=sys.stderr)
                    else:
                        validated[key] = value
                else:
                    print(f"  [CONFIG] Warning: '{key}' has wrong type (expected {expected_type.__name__}), using default.", file=sys.stderr)
        
        return {**CONFIG_DEFAULTS, **validated}
    except (json.JSONDecodeError, OSError):
        return dict(CONFIG_DEFAULTS)


def save_config(cfg):
    """
    Persist config to disk atomically (temp file + rename).

    A non-atomic write_text would corrupt the config on a mid-write crash.
    Issue 25 note: returns False on failure so callers can react.
    """
    try:
        import tempfile
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=CONFIG_FILE.parent, suffix=".tmp", prefix=".config_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(cfg, indent=2))
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, CONFIG_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True
    except Exception as e:
        print(f"  {clr.R}Config write failed:{clr.RST} {e}", file=sys.stderr)
        return False


def _config_key_valid(key):
    return key in CONFIG_DEFAULTS


def cmd_doctor(cfg):
    import subprocess
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
        files = sorted(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
        if not files:
            print(f"  {clr.DIM}Cache is empty.{clr.RST}  {clr.W}{CACHE_DIR}{clr.RST}")
            return
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
                folder_path = str(Path(data[0]["path"]).parent) if data else "?"
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
            from ._cache import _cache_key
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
                if key == "top" and not (0 <= coerced <= 100):
                    print(
                        f"  {clr.R}[ERROR]{clr.RST} 'top' must be between 0 and 100.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
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

