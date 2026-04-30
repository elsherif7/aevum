"""
Security validation utilities for Aevum.

Provides path validation, input sanitization, and access control
to prevent command injection, path traversal, and unauthorized access.
"""

import os
from pathlib import Path
from typing import List


# Allowed scan roots - user directories and common media locations
def _get_allowed_scan_roots() -> List[Path]:
    """Get list of allowed root directories for scanning."""
    roots = [
        Path.home(),
        Path("/media"),
        Path("/mnt"),
    ]
    
    # On Windows, add common media drives
    if os.name == "nt":
        for drive in "DEFGHIJ":
            drive_path = Path(f"{drive}:\\")
            if drive_path.exists():
                roots.append(drive_path)
    
    return [r for r in roots if r.exists()]


ALLOWED_SCAN_ROOTS = _get_allowed_scan_roots()


def validate_scan_path(folder_path: str) -> Path:
    """
    Validate that scan path is safe and accessible.
    
    Security: Prevents directory traversal and scanning of sensitive directories.
    
    Args:
        folder_path: User-provided path to scan
        
    Returns:
        Resolved, validated Path object
        
    Raises:
        PermissionError: If path is restricted or outside allowed roots
        ValueError: If path is not a directory
    """
    try:
        # Resolve path and follow symlinks to prevent TOCTOU
        path = Path(folder_path).resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise PermissionError(f"Invalid path: {e}")
    
    # Check if path is under allowed roots
    is_allowed = any(
        _is_relative_to(path, root)
        for root in ALLOWED_SCAN_ROOTS
    )
    
    if not is_allowed:
        raise PermissionError(
            f"Cannot scan {path}. Access restricted to user directories."
        )
    
    # Prevent scanning sensitive directories even under allowed roots
    forbidden_patterns = [
        ".ssh",
        ".gnupg", 
        ".password-store",
        "wallet",
        "keychain",
        ".aws",
        ".kube",
    ]
    
    for part in path.parts:
        if any(pattern in part.lower() for pattern in forbidden_patterns):
            raise PermissionError(
                f"Cannot scan sensitive directory: {part}"
            )
    
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")
    
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    """
    Check if path is under root directory.
    
    Compatibility wrapper for Python 3.8 (is_relative_to added in 3.9).
    """
    try:
        # Python 3.9+
        return path.is_relative_to(root)
    except AttributeError:
        # Python 3.8 fallback
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False


def validate_export_path(out_path: str, scan_folder: Path) -> Path:
    """
    Validate export destination path to prevent arbitrary writes.
    
    Security: Prevents path traversal and overwriting system files.
    
    Args:
        out_path: User-provided output path
        scan_folder: The folder being scanned (for context)
        
    Returns:
        Validated Path object
        
    Raises:
        PermissionError: If path is outside allowed directories
        ValueError: If file extension is invalid
    """
    out_path = Path(out_path).resolve()
    
    # Define allowed export directories
    allowed_dirs = [
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Downloads",
        scan_folder.parent,  # Allow sibling to scan folder
    ]
    
    # Check if output path is under allowed directories
    is_allowed = any(
        _is_relative_to(out_path, allowed_dir)
        for allowed_dir in allowed_dirs
        if allowed_dir.exists()
    )
    
    if not is_allowed:
        raise PermissionError(
            f"Cannot write to {out_path}. "
            f"Only user directories are allowed."
        )
    
    # Validate file extension
    allowed_extensions = {'.txt', '.csv', '.json'}
    if out_path.suffix.lower() not in allowed_extensions:
        raise ValueError(
            f"Invalid extension {out_path.suffix}. "
            f"Allowed: {', '.join(allowed_extensions)}"
        )
    
    return out_path


def validate_input_length(value: str, max_length: int, name: str) -> str:
    """
    Validate input string length to prevent memory exhaustion.
    
    Args:
        value: Input string
        max_length: Maximum allowed length
        name: Input name for error message
        
    Returns:
        Validated string
        
    Raises:
        ValueError: If input too long
    """
    if len(value) > max_length:
        raise ValueError(
            f"{name} too long: {len(value)} chars (max: {max_length})"
        )
    return value


# Constants for length validation
MAX_PATH_LENGTH = 4096
MAX_ALIAS_LENGTH = 50
MAX_FILENAME_LENGTH = 255


def validate_alias_name(name: str) -> str:
    """
    Validate alias name for security and usability.
    
    Security: Prevents excessively long names and special characters.
    
    Args:
        name: Alias name to validate
        
    Returns:
        Validated alias name
        
    Raises:
        ValueError: If alias name is invalid
    """
    if not name:
        raise ValueError("Alias name cannot be empty")
    
    if len(name) > MAX_ALIAS_LENGTH:
        raise ValueError(
            f"Alias name too long: {len(name)} chars (max: {MAX_ALIAS_LENGTH})"
        )
    
    # Only allow alphanumeric and safe characters
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise ValueError(
            "Alias name can only contain letters, numbers, hyphens, and underscores"
        )
    
    return name
