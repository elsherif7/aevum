"""
Secure API key storage for Aevum.

Uses system keyring (encrypted) instead of plaintext files.
Falls back to encrypted file storage if keyring is unavailable.
"""

import os
import re
import sys
from pathlib import Path

# Try to import keyring (optional dependency)
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    keyring = None

from ._paths import YT_KEY_FILE

SERVICE_NAME = "aevum-media-scanner"
KEY_NAME     = "youtube-api-key"

# H-01: compile once at module level — not inside save_api_key on every call
_YT_KEY_PATTERN = re.compile(r'^AIza[0-9A-Za-z\-_]{35}$')


def _get_cipher():
    """
    Get Fernet cipher for fallback encrypted-file storage.

    S-01 fix: The previous implementation derived the encryption key from
    hostname+username (both trivially known to any local user), making the
    encryption provide only marginal protection over plaintext.

    The new implementation generates a truly random 32-byte key and stores
    it in a separate key file with 0o600 permissions.  This is still a
    best-effort fallback — the system keyring (Method 1) remains the only
    genuinely secure option — but it is no longer trivially reversible by
    any local user who can read the ciphertext file.
    """
    try:
        from cryptography.fernet import Fernet
        import secrets as _secrets

        key_file = YT_KEY_FILE.with_suffix(".key")

        # Load existing random key or create a fresh one
        if key_file.exists():
            try:
                raw_key = key_file.read_bytes()
                # Fernet keys are 44 bytes when base64-encoded
                if len(raw_key) != 44:
                    raise ValueError("bad key length")
                # Validate it is a valid Fernet key by constructing one
                Fernet(raw_key)
                return Fernet(raw_key)
            except Exception:
                pass  # fall through to generate a new key

        # Generate a fresh random Fernet key
        new_key = Fernet.generate_key()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(new_key)
        os.chmod(key_file, 0o600)
        return Fernet(new_key)
    except (ImportError, OSError, ValueError):
        return None


def save_api_key(api_key: str) -> bool:
    """
    Store API key securely.
    
    Security: Tries to use system keyring (encrypted), falls back to
    encrypted file if keyring unavailable, warns if storing plaintext.
    
    Args:
        api_key: YouTube API key to store
        
    Returns:
        True if saved successfully, False otherwise
    """
    # S-02: Validate API key format (YouTube keys start with AIza)
    # H-01: use pre-compiled regex (module-level constant)
    if not api_key or not _YT_KEY_PATTERN.match(api_key):
        print(f"  Error: Invalid API key format (expected AIza...)", file=sys.stderr)
        return False
    
    # Method 1: System keyring (best - OS-encrypted storage)
    if KEYRING_AVAILABLE:
        try:
            keyring.set_password(SERVICE_NAME, KEY_NAME, api_key)
            
            # Remove old plaintext file if exists
            if YT_KEY_FILE.exists():
                try:
                    YT_KEY_FILE.unlink()
                    print(f"  [MIGRATED] Moved API key to secure system keyring")
                except OSError:
                    pass
            
            return True
        except Exception as e:
            print(f"  Warning: Keyring failed ({e}), trying fallback...", 
                  file=sys.stderr)
    
    # Method 2: Encrypted file (fallback)
    cipher = _get_cipher()
    if cipher:
        try:
            YT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            
            encrypted = cipher.encrypt(api_key.encode('utf-8'))
            
            # Write with restrictive permissions
            YT_KEY_FILE.write_bytes(encrypted)
            os.chmod(YT_KEY_FILE, 0o600)
            
            print(f"  [ENCRYPTED] API key stored in encrypted file")
            return True
        except Exception as e:
            print(f"  Warning: Encryption failed ({e}), trying plaintext...", 
                  file=sys.stderr)
    
    # Method 3: Plaintext (worst case - warn user)
    try:
        YT_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        YT_KEY_FILE.write_text(api_key, encoding='utf-8')
        os.chmod(YT_KEY_FILE, 0o600)
        
        print(f"  [WARN] API key stored in PLAINTEXT", file=sys.stderr)
        print(f"  Install 'keyring' for secure storage: pip install keyring", 
              file=sys.stderr)
        return True
    except Exception as e:
        print(f"  Error: Could not save API key: {e}", file=sys.stderr)
        return False


def load_api_key() -> str:
    """
    Load API key from secure storage.
    
    Security: Tries keyring first, then encrypted file, then plaintext fallback.
    
    Returns:
        API key string, or empty string if not found
    """
    # Method 1: System keyring
    if KEYRING_AVAILABLE:
        try:
            key = keyring.get_password(SERVICE_NAME, KEY_NAME)
            if key:
                return key
        except Exception:
            pass
    
    # Method 2: Encrypted file
    if YT_KEY_FILE.exists():
        try:
            cipher = _get_cipher()
            if cipher:
                encrypted = YT_KEY_FILE.read_bytes()
                # Always attempt decryption when cipher is available.
                # Only fall back to plaintext if cipher is None.
                try:
                    decrypted = cipher.decrypt(encrypted)
                    return decrypted.decode('utf-8').strip()
                except Exception:
                    # Decryption failed — file may be plaintext (pre-encryption migration)
                    # Only treat as plaintext if it looks like a valid API key
                    try:
                        candidate = encrypted.decode('utf-8', errors='replace').strip()
                        if _YT_KEY_PATTERN.match(candidate):
                            return candidate
                    except Exception:
                        pass
                    return ""
        except Exception:
            pass

        # Method 3: Plaintext fallback (no cipher available)
        try:
            return YT_KEY_FILE.read_text(encoding='utf-8').strip()
        except Exception:
            pass
    
    return ""


def delete_api_key() -> bool:
    """
    Remove API key from all storage locations.
    
    Returns:
        True if deleted successfully
    """
    success = True
    
    # Remove from keyring
    if KEYRING_AVAILABLE:
        try:
            keyring.delete_password(SERVICE_NAME, KEY_NAME)
        except Exception:
            pass
    
    # Remove file
    if YT_KEY_FILE.exists():
        try:
            YT_KEY_FILE.unlink()
        except OSError:
            success = False
    
    return success


def get_storage_method() -> str:
    """
    Return current storage method for informational purposes.

    Detects encrypted vs plaintext by file size: Fernet-encrypted 39-char
    keys produce ~100+ byte ciphertext; plaintext keys are ≤100 bytes.
    No decryption required.

    Returns:
        "keyring", "encrypted_file", "plaintext_file", or "none"
    """
    if KEYRING_AVAILABLE:
        try:
            if keyring.get_password(SERVICE_NAME, KEY_NAME):
                return "keyring"
        except Exception:
            pass

    if YT_KEY_FILE.exists():
        try:
            # Fernet-encrypted 39-char key → ~116 bytes ciphertext.
            # Plaintext key → 39 bytes. 80 bytes is a safe boundary.
            size = YT_KEY_FILE.stat().st_size
            return "encrypted_file" if size > 80 else "plaintext_file"
        except OSError:
            pass

    return "none"
