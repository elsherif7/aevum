"""
Security audit logging for Aevum.

Logs security-relevant events for monitoring and forensics.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from ._paths import APPDATA

AUDIT_LOG = APPDATA / "audit.log"

# Configure audit logger (separate from main logger).
# Guard against duplicate handlers if the module is reloaded (e.g. in tests).
audit_logger = logging.getLogger("aevum.audit")
audit_logger.setLevel(logging.INFO)

if not audit_logger.handlers:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        _handler = logging.FileHandler(AUDIT_LOG, encoding="utf-8")
        _handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        audit_logger.addHandler(_handler)
    except OSError:
        pass


def audit_log(event: str, details: dict = None):
    """
    Log security-relevant event to audit trail.
    
    Args:
        event: Event type (e.g., 'SCAN_START', 'EXPORT', 'CONFIG_CHANGE')
        details: Additional event details
    """
    try:
        # Get username safely
        try:
            username = os.getlogin()
        except (OSError, AttributeError):
            username = os.getenv('USER') or os.getenv('USERNAME') or 'unknown'
        
        # Build log entry
        entry = {
            'timestamp': datetime.now().isoformat(),
            'event': event,
            'user': username,
            'details': details or {}
        }
        
        # Log as JSON for easy parsing
        audit_logger.info(json.dumps(entry))
    except Exception:
        # Never let audit logging break the app
        pass


def audit_scan(path: str, file_count: int):
    """Log scan operation."""
    audit_log('SCAN', {
        'path': str(path),
        'file_count': file_count
    })


def audit_export(source: str, destination: str, format: str):
    """Log export operation."""
    audit_log('EXPORT', {
        'source': str(source),
        'destination': str(destination),
        'format': format
    })


def audit_config_change(key: str, old_value: str, new_value: str):
    """Log configuration change."""
    # Don't log actual API keys
    if 'api_key' in key.lower():
        old_value = '(hidden)'
        new_value = '(hidden)'
    
    audit_log('CONFIG_CHANGE', {
        'key': key,
        'old_value': str(old_value),
        'new_value': str(new_value)
    })


def audit_api_key_change(action: str):
    """Log API key changes (set, delete, migrate)."""
    audit_log('API_KEY', {
        'action': action
    })


def audit_cache_clear(scope: str):
    """Log cache clear operations."""
    audit_log('CACHE_CLEAR', {
        'scope': scope
    })


def audit_permission_denied(operation: str, path: str, reason: str):
    """Log permission denied events."""
    audit_log('PERMISSION_DENIED', {
        'operation': operation,
        'path': str(path),
        'reason': reason
    })


def get_recent_audit_events(limit: int = 50) -> list:
    """
    Read recent audit events from log.
    
    Args:
        limit: Maximum number of events to return
        
    Returns:
        List of audit event dicts (newest first)
    """
    if not AUDIT_LOG.exists():
        return []
    
    try:
        events = []
        with AUDIT_LOG.open('r', encoding='utf-8') as f:
            for line in f:
                try:
                    # Parse log line - format: "timestamp - level - json"
                    if ' - INFO - ' in line:
                        json_part = line.split(' - INFO - ', 1)[1]
                        event = json.loads(json_part)
                        events.append(event)
                except (json.JSONDecodeError, IndexError):
                    continue
        
        # Return newest first
        return events[-limit:][::-1]
    except OSError:
        return []
