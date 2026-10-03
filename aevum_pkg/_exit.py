"""Exit codes, in their own module so any module can import them without a cycle through _cli."""


class EX:
    OK         = 0
    ERR_ARGS   = 1  # bad arguments, path not found, not a folder
    ERR_DEPS   = 2  # ffprobe not on PATH
    ERR_SCAN   = 3  # scan failed or was interrupted
    ERR_API    = 5  # YouTube API error or bad key
