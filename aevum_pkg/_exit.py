"""
Exit codes for Aevum CLI.

Q-02 fix: extracted from _cli.py to avoid circular imports and make
exit codes importable from any module.
"""


class EX:
    """Exit codes used throughout Aevum."""
    OK         = 0  # Success
    ERR_ARGS   = 1  # Bad arguments / path not found / not a directory
    ERR_DEPS   = 2  # Missing dependency (ffprobe not on PATH)
    ERR_SCAN   = 3  # Scan failed / interrupted
    ERR_API    = 5  # YouTube API error / auth failure
