"""
Named exit codes for Aevum.
Use these everywhere instead of bare integers so scripts can rely on them.

  0  OK          — success
  1  ERR_ARGS    — bad arguments / path not found / not a directory
  2  ERR_DEPS    — missing dependency (ffprobe not on PATH)
  3  ERR_SCAN    — scan failed / interrupted
  4  ERR_EXPORT  — export / write failed
  5  ERR_API     — YouTube API error / auth failure
"""

OK         = 0
ERR_ARGS   = 1
ERR_DEPS   = 2
ERR_SCAN   = 3
ERR_EXPORT = 4
ERR_API    = 5
