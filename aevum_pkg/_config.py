import json
import os
import sys
from pathlib import Path

from ._color import clr
from ._paths import CONFIG_FILE

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

                if isinstance(value, expected_type):
                    if key == "top":
                        if 0 <= value <= 100:
                            validated[key] = value
                        else:
                            print(f"  [CONFIG] Warning: 'top' value {value!r} out of range (0-100), using default.", file=sys.stderr)
                    elif key == "sort":
                        # B-06: normalize bare sort names (e.g. "duration" → "duration:desc")
                        if ':' not in value:
                            _sort_defaults = {'name': 'asc', 'duration': 'desc', 'count': 'desc'}
                            value = value + ':' + _sort_defaults.get(value, 'asc')
                        valid_sorts = [
                            "name:asc", "name:desc",
                            "duration:asc", "duration:desc",
                            "count:asc", "count:desc",
                        ]
                        if value in valid_sorts:
                            validated[key] = value
                        else:
                            print(f"  [CONFIG] Warning: 'sort' value {value!r} is not valid, using default.", file=sys.stderr)
                    elif key == "aliases":
                        if isinstance(value, dict):
                            clean_aliases = {}
                            for k, v in value.items():
                                if isinstance(k, str) and isinstance(v, str):
                                    if len(k) <= 50 and len(v) <= 4096:
                                        clean_aliases[k] = v
                                    else:
                                        print(f"  [CONFIG] Warning: alias '{k}' exceeds length limit, skipped.", file=sys.stderr)
                            validated[key] = clean_aliases
                    elif key == "project_dir":
                        if not value or Path(value).is_absolute():
                            validated[key] = value
                        else:
                            print("  [CONFIG] Warning: 'project_dir' must be an absolute path, using default.", file=sys.stderr)
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

