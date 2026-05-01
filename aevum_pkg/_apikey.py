"""
Secure API key storage for Aevum.

Uses system keyring (encrypted) instead of plaintext files.
Falls back to encrypted file storage if keyring is unavailable.
"""

import base64
import os
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
KEY_NAME = "youtube-api-key"


def _get_cipher():
    """Get Fernet cipher for fallback encryption."""
    try:
        from cryptography.fernet import Fernet
        
        # Use machine-specific key derived from hostname + username
        # This isn't perfect security, but better than plaintext
        import hashlib
        import socket
        
        machine_id = f"{socket.gethostname()}-{os.getlogin()}"
        key_material = hashlib.sha256(machine_id.encode()).digest()
        key = base64.urlsafe_b64encode(key_material)
        
        return Fernet(key)
    except ImportError:
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
    if not api_key or len(api_key) < 30:
        print(f"  Error: Invalid API key format", file=sys.stderr)
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
        
        print(f"  ⚠️  WARNING: API key stored in PLAINTEXT", file=sys.stderr)
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
                decrypted = cipher.decrypt(encrypted)
                return decrypted.decode('utf-8')
        except Exception:
            # Maybe it's plaintext, try that
            pass
        
        # Method 3: Plaintext fallback
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
        cipher = _get_cipher()
        if cipher:
            try:
                # Try to decrypt - if it works, it's encrypted
                encrypted = YT_KEY_FILE.read_bytes()
                cipher.decrypt(encrypted)
                return "encrypted_file"
            except Exception:
                return "plaintext_file"
    
    return "none"
