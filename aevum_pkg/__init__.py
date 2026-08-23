"""
aevum_pkg — Aevum is a CLI-only tool, not a maintained Python library.

This package is not intended to be imported and used as an API by
other code. Every internal module imports directly from its specific
submodule (._scan, ._youtube, ._display, etc.) rather than through
this file, so nothing here is required for the CLI to run except the
version string below.
"""

__version__ = "1.0.0"
