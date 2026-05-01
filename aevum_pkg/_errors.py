"""
Secure error handling for Aevum.

Prevents information disclosure via error messages while maintaining
useful debugging information in logs.
"""

import logging
import sys
from typing import Optional

from ._paths import APPDATA

_log_path = APPDATA / "aevum.log"

try:
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(_log_path, encoding="utf-8")
except OSError:
    _file_handler = None

# Use explicit handler attachment instead of basicConfig — basicConfig is a
# no-op if any other module has already configured the root logger first.
logger = logging.getLogger('aevum')
if not logger.handlers:
    logger.setLevel(logging.WARNING)
    _fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setFormatter(_fmt)
    logger.addHandler(_stderr_handler)
    if _file_handler:
        _file_handler.setFormatter(_fmt)
        logger.addHandler(_file_handler)


def safe_error_message(error: Exception, user_friendly: str, 
                       log_details: bool = True) -> str:
    """
    Return user-friendly error without exposing system details.
    
    Security: Logs full error for debugging but shows sanitized message to user.
    
    Args:
        error: The actual exception
        user_friendly: Safe message to show user
        log_details: Whether to log full error details
        
    Returns:
        User-friendly error message
    """
    if log_details:
        # Log detailed error for admin/debugging
        logger.error(
            f"Internal error: {type(error).__name__}: {error}",
            exc_info=True
        )
    
    return user_friendly


def sanitize_path_in_message(message: str) -> str:
    """
    Remove absolute paths from error messages.
    
    Security: Prevents disclosure of system directory structure.
    """
    import re
    
    # Replace Windows paths (C:\Users\..., D:\...)
    message = re.sub(
        r'[A-Z]:\\(?:Users\\[^\\]+\\|[^\\]+\\)*',
        '<path>/',
        message
    )
    
    # Replace Unix paths (/home/user/..., /root/...)
    message = re.sub(
        r'/(?:home|root|Users)/[^/]+/',
        '<path>/',
        message
    )
    
    return message


class SafePermissionError(PermissionError):
    """Permission error with sanitized message."""
    
    def __init__(self, user_message: str, original_error: Optional[Exception] = None):
        super().__init__(user_message)
        if original_error:
            logger.error(f"Permission denied: {original_error}")


class SafeFileNotFoundError(FileNotFoundError):
    """File not found error with sanitized message."""
    
    def __init__(self, user_message: str, original_error: Optional[Exception] = None):
        super().__init__(user_message)
        if original_error:
            logger.error(f"File not found: {original_error}")


class SafeValueError(ValueError):
    """Value error with sanitized message."""
    
    def __init__(self, user_message: str, original_error: Optional[Exception] = None):
        super().__init__(user_message)
        if original_error:
            logger.error(f"Invalid value: {original_error}")
