"""
Shared data models for Aevum.

Defining the tree structures here — rather than as raw tuples — gives every
call site self-documenting field names, makes static analysis possible, and
means adding a field only requires changing one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# FolderNode
# ---------------------------------------------------------------------------
# Represents one node in the scanned folder tree.
#
# Fields
# ------
# name          Folder name (leaf only, not full path)
# total_sec     Total duration of all media under this folder (seconds)
# total_count   Total number of media files under this folder
# total_bytes   Total size of all media under this folder (bytes)
# children      Recursively nested FolderNode list for subfolders
# direct_files  List of (Path, seconds) for files directly in this folder
# ---------------------------------------------------------------------------

class FolderNode(NamedTuple):
    name:         str
    total_sec:    float
    total_count:  int
    total_bytes:  int
    children:     list[FolderNode]
    direct_files: list[tuple[Path, float]]


# ---------------------------------------------------------------------------
# ScanTree
# ---------------------------------------------------------------------------
# Top-level result of _build_tree: the root's children, the files sitting
# directly in the root, and the root's total byte count.
#
# Previously this was a raw 3-tuple (subfolders, direct, root_bytes) which
# forced every call site to remember the positional meaning of each element.
# ---------------------------------------------------------------------------

class ScanTree(NamedTuple):
    children:     list[FolderNode]
    direct_files: list[tuple[Path, float]]
    root_bytes:   int
