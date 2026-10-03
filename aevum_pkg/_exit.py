"""
Exit codes for Aevum CLI.

Kept in its own module so any module can import the exit codes without
creating a circular import with _cli.py.
"""


class EX:
    """Exit codes used throughout Aevum."""
    OK         = 0  # Success
    ERR_ARGS   = 1  # Bad arguments / path not found / not a directory
    ERR_DEPS   = 2  # Missing dependency (ffprobe not on PATH)
    ERR_SCAN   = 3  # Scan failed / interrupted
    ERR_API    = 5  # YouTube API error / auth failure
