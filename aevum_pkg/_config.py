import json
import os
import subprocess
import sys
from pathlib import Path

from ._color  import R, G, Y, C, W, DIM, RST, LINE
from ._cache  import CACHE_DIR, _cache_key
from ._scan   import format_size, check_ffprobe
from ._youtube import load_api_key, save_api_key, prompt_api_key

CONFIG_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Aevum" / "config.json"

CONFIG_DEFAULTS = {
    "sort":          "name:asc",
    "top":           10,
    "no_color":      False,
    "cache_enabled": True,
    "export_dir":    "",
}


def load_config():
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {**CONFIG_DEFAULTS, **data}
    except Exception:
        return dict(CONFIG_DEFAULTS)


def save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  {R}Config write failed:{RST} {e}", file=sys.stderr)


def _config_key_valid(key):
    return key in CONFIG_DEFAULTS


def cmd_doctor(cfg):
    print()
    print(f"  {C}{LINE}{RST}")
    print(f"  {C}  Aevum Doctor{RST}  {DIM}|{RST}  {W}Environment Check{RST}")
    print(f"  {C}{LINE}{RST}")
    print()

    pv = sys.version.split()[0]
    print(f"  {G}[OK]{RST}   Python {pv}")

    try:
        r  = subprocess.run(['ffprobe', '-version'], capture_output=True, text=True)
        fv = r.stdout.splitlines()[0] if r.stdout else "unknown"
        print(f"  {G}[OK]{RST}   {fv}")
    except FileNotFoundError:
        print(f"  {R}[FAIL]{RST}  ffprobe not found on PATH")
        print(f"         Install FFmpeg: {C}https://ffmpeg.org/download.html{RST}")

    api_key = load_api_key()
    if api_key:
        masked = api_key[:6] + '...' + api_key[-4:] if len(api_key) > 10 else '***'
        print(f"  {G}[OK]{RST}   YouTube API key set  {DIM}({masked}){RST}")
    else:
        print(f"  {Y}[WARN]{RST}  YouTube API key not set")
        print(f"         Set it with: {W}aevum config set yt_api_key <key>{RST}")

    try:
        files       = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
        total_bytes = sum(f.stat().st_size for f in files)
        print(f"  {G}[OK]{RST}   Cache: {len(files)} entries, {format_size(total_bytes)} at {CACHE_DIR}")
    except Exception:
        print(f"  {Y}[WARN]{RST}  Could not read cache directory: {CACHE_DIR}")

    if CONFIG_FILE.exists():
        print(f"  {G}[OK]{RST}   Config: {CONFIG_FILE}")
    else:
        print(f"  {DIM}[INFO]{RST}  No config file (using defaults). {DIM}{CONFIG_FILE}{RST}")
    print()


def cmd_cache(args):
    action = args.action or 'list'

    if action == 'path':
        print(f"  {W}{CACHE_DIR}{RST}")
        return

    if action == 'list':
        if not CACHE_DIR.exists() or not list(CACHE_DIR.glob("*.json")):
            print(f"  {DIM}Cache is empty.{RST}  {W}{CACHE_DIR}{RST}")
            return
        files = sorted(CACHE_DIR.glob("*.json"))
        print()
        print(f"  {C}{LINE}{RST}")
        print(f"  {W}  Cache  {DIM}|{RST}  {CACHE_DIR}{RST}")
        print(f"  {C}{LINE}{RST}")
        print()
        total = 0
        for f in files:
            sz    = f.stat().st_size
            total += sz
            try:
                import json as _json
                data        = _json.loads(f.read_text(encoding='utf-8'))
                folder_path = data[0]['path'].rsplit('\\', 1)[0] if data else '?'
                count       = len(data)
            except Exception:
                folder_path = '?'
                count       = 0
            print(f"  {DIM}{f.name[:16]}{RST}  {W}{folder_path}{RST}  {DIM}({count} files, {format_size(sz)}){RST}")
        print()
        print(f"  {DIM}Total: {len(files)} cache files, {format_size(total)}{RST}")
        print()
        return

    if action == 'clear':
        target_folder = getattr(args, 'folder', None)
        if target_folder:
            key = _cache_key(target_folder)
            if key.exists():
                key.unlink()
                print(f"  {G}[OK]{RST}  Cleared cache for {target_folder}")
            else:
                print(f"  {DIM}[SKIP]{RST}  No cache found for {target_folder}")
        else:
            if not CACHE_DIR.exists():
                print(f"  {DIM}Cache is already empty.{RST}")
                return
            files = list(CACHE_DIR.glob("*.json"))
            for f in files:
                f.unlink()
            print(f"  {G}[OK]{RST}  Cleared {len(files)} cache files from {CACHE_DIR}")


def cmd_config(args, cfg):
    action = args.action
    YT_KEY = 'yt_api_key'

    if action == 'list':
        print()
        print(f"  {C}{LINE}{RST}")
        print(f"  {W}  Configuration{RST}  {DIM}|{RST}  {CONFIG_FILE}")
        print(f"  {C}{LINE}{RST}")
        print()
        for k, v in cfg.items():
            print(f"  {G}{k:<18}{RST}  {W}{v}{RST}")
        api_key = load_api_key()
        masked  = (api_key[:6] + '...' + api_key[-4:]) if api_key and len(api_key) > 10 else (api_key or '(not set)')
        print(f"  {G}{YT_KEY:<18}{RST}  {W}{masked}{RST}")
        print()
        return

    if action == 'reset':
        save_config(dict(CONFIG_DEFAULTS))
        print(f"  {G}[OK]{RST}  Configuration reset to defaults.")
        return

    key = args.key
    if not key:
        print(f"  {R}[ERROR]{RST} Key required. Run 'aevum config list' to see all keys.", file=sys.stderr)
        sys.exit(1)

    if action == 'get':
        if key == YT_KEY:
            api_key = load_api_key()
            print(api_key or '(not set)')
        elif _config_key_valid(key):
            print(cfg.get(key, CONFIG_DEFAULTS.get(key)))
        else:
            print(f"  {R}[ERROR]{RST} Unknown key: {key}", file=sys.stderr)
            sys.exit(1)
        return

    if action == 'set':
        value = args.value
        if key == YT_KEY:
            if not value:
                prompt_api_key()
                return
            save_api_key(value)
            print(f"  {G}[OK]{RST}  yt_api_key saved.")
            return
        if not _config_key_valid(key):
            print(f"  {R}[ERROR]{RST} Unknown key: {key}. Run 'aevum config list' to see all keys.", file=sys.stderr)
            sys.exit(1)
        if value is None:
            print(f"  {R}[ERROR]{RST} Value required: aevum config set {key} <value>", file=sys.stderr)
            sys.exit(1)
        default = CONFIG_DEFAULTS[key]
        try:
            if isinstance(default, bool):
                coerced = value.lower() in ('1', 'true', 'yes')
            elif isinstance(default, int):
                coerced = int(value)
            else:
                coerced = value
        except (ValueError, AttributeError):
            print(f"  {R}[ERROR]{RST} Invalid value for {key}: {value}", file=sys.stderr)
            sys.exit(1)
        cfg[key] = coerced
        save_config(cfg)
        print(f"  {G}[OK]{RST}  {key} = {coerced}")


def repl_config(parts, cfg):
    import types
    if not parts:
        print(f"  {DIM}Usage: config get <key> | config set <key> <value> | config list | config reset{RST}\n")
        return
    ns = types.SimpleNamespace(
        action=parts[0] if parts else 'list',
        key=parts[1]    if len(parts) > 1 else None,
        value=parts[2]  if len(parts) > 2 else None,
        no_color=False,
    )
    cmd_config(ns, cfg)
