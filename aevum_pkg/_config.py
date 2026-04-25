import json
import os
import subprocess
import sys
from pathlib import Path

from ._color  import clr, LINE
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
        print(f"  {clr.R}Config write failed:{clr.RST} {e}", file=sys.stderr)


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
        masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"  {clr.G}[OK]{clr.RST}   YouTube API key set  {clr.DIM}({masked}){clr.RST}")
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
                folder_path = data[0]["path"].rsplit("\\", 1)[0] if data else "?"
                count       = len(data)
            except Exception:
                folder_path = "?"
                count       = 0
            print(f"  {clr.DIM}{f.name[:16]}{clr.RST}  {clr.W}{folder_path}{clr.RST}  {clr.DIM}({count} files, {format_size(sz)}){clr.RST}")
        print()
        print(f"  {clr.DIM}Total: {len(files)} cache files, {format_size(total)}{clr.RST}")
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
            files = list(CACHE_DIR.glob("*.json"))
            for f in files:
                f.unlink()
            print(f"  {clr.G}[OK]{clr.RST}  Cleared {len(files)} cache files from {CACHE_DIR}")


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
        masked  = (api_key[:6] + "..." + api_key[-4:]) if api_key and len(api_key) > 10 else (api_key or "(not set)")
        print(f"  {clr.G}{YT_KEY:<18}{clr.RST}  {clr.W}{masked}{clr.RST}")
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
            print(api_key or "(not set)")
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
            save_api_key(value)
            print(f"  {clr.G}[OK]{clr.RST}  yt_api_key saved.")
            return
        if not _config_key_valid(key):
            print(f"  {clr.R}[ERROR]{clr.RST} Unknown key: {key}. Run 'aevum config list' to see all keys.", file=sys.stderr)
            sys.exit(1)
        if value is None:
            print(f"  {clr.R}[ERROR]{clr.RST} Value required: aevum config set {key} <value>", file=sys.stderr)
            sys.exit(1)
        default = CONFIG_DEFAULTS[key]
        try:
            if isinstance(default, bool):
                coerced = value.lower() in ("1", "true", "yes")
            elif isinstance(default, int):
                coerced = int(value)
            else:
                coerced = value
        except (ValueError, AttributeError):
            print(f"  {clr.R}[ERROR]{clr.RST} Invalid value for {key}: {value}", file=sys.stderr)
            sys.exit(1)
        cfg[key] = coerced
        save_config(cfg)
        print(f"  {clr.G}[OK]{clr.RST}  {key} = {coerced}")


def repl_config(parts, cfg):
    import types
    if not parts:
        print(f"  {clr.DIM}Usage: config get <key> | config set <key> <value> | config list | config reset{clr.RST}\n")
        return
    ns = types.SimpleNamespace(
        action=parts[0] if parts else "list",
        key=parts[1]    if len(parts) > 1 else None,
        value=parts[2]  if len(parts) > 2 else None,
        no_color=False,
    )
    cmd_config(ns, cfg)
