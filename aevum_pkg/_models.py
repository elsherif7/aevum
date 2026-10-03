from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class FolderNode(NamedTuple):
    """One folder in the scanned tree. The totals include everything below it."""
    name:         str
    total_sec:    float
    total_count:  int
    total_bytes:  int
    children:     list[FolderNode]
    direct_files: list[tuple[Path, float]]  # (path, seconds) for files directly in this folder


class ScanTree(NamedTuple):
    """The root's subfolders, the files directly in the root, and the root's total size."""
    children:     list[FolderNode]
    direct_files: list[tuple[Path, float]]
    root_bytes:   int
