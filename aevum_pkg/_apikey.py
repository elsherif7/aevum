"""
YouTube API key storage for Aevum.

Simplest practical option: the key is saved once to a single local file
with restrictive permissions (0o600, owner read/write only) so it
doesn't need to be re-entered on every run. This replaces the previous
three-tier keyring -> encrypted-file -> plaintext fallback chain — that
extra machinery added real complexity for a single-user local CLI tool
where the OS's own file permissions are already the practical boundary.
"""

import os
import re
import sys

from ._paths import YT_KEY_FILE

# H-01: compile once at module level.
_YT_KEY_PATTERN = re.compile(r'^AIza[0-9A-Za-z\-_]{35}$')


def save_api_key(api_key: str) -> bool:
    """
    Store the API key in a local file, owner-only permissions.
    Returns True if saved successfully, False otherwise.
    """
    # S-02: validate API key format (YouTube keys start with AIza).
    if not api_key or not _YT_KEY_PATTERN.match(api_key):
        print("  Error: Invalid API key format (expected AIza...)", file=sys.stderr)
        return False

    try:
        YT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        YT_KEY_FILE.write_text(api_key, encoding='utf-8')
        os.chmod(YT_KEY_FILE, 0o600)
        return True
    except Exception as e:
        print(f"  Error: Could not save API key: {e}", file=sys.stderr)
        return False


def load_api_key() -> str:
    """Load the API key from local storage. Returns "" if not found."""
    try:
        return YT_KEY_FILE.read_text(encoding='utf-8').strip()
    except Exception:
        return ""
