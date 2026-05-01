# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Patrick R. Wallace, Hamilton College LITS
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.  See <https://www.gnu.org/licenses/gpl-3.0.html>.
#
# AI ASSISTANCE NOTICE: Developed with assistance from Claude (Anthropic).
# Reviewed and tested; verify behavior in your own environment.

"""
manifest.py — Content-hash manifest for tracking processed ingest files.

Design
------
Files are identified by the MD5 hash of their *contents*, not their names or
paths.  This means:

  - Renaming or moving a file within ingest/ does not cause it to be
    reprocessed (the hash is unchanged).
  - Modifying a file (even trivially) causes it to be treated as new content
    (the hash changes).  This is intentional: changed content should be
    re-evaluated.

The manifest is stored as a JSON file (.manifest.json at the wiki root) and
is intended to be committed to git so that ingest history persists across
machines and clones.

Manifest entry schema
---------------------
Each entry is keyed by the file's MD5 hex digest:

    {
        "<md5-hex>": {
            "filename":   str,   # original filename at time of processing
            "path":       str,   # full path at time of processing
            "processed":  str,   # ISO 8601 UTC datetime
            "wiki_page":  str    # path of the wiki/sources/ page created
        },
        ...
    }

Usage (import)
--------------
    from scripts.manifest import load_manifest, is_processed, mark_processed, save_manifest

Usage (CLI — list all processed files)
---------------------------------------
    python scripts/manifest.py
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow the script to be run directly from the wiki root or imported as a
# module from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.config import MANIFEST_FILE


# ---------------------------------------------------------------------------
# Core utilities
# ---------------------------------------------------------------------------

def file_hash(filepath: Path) -> str:
    """Return the MD5 hex digest of *filepath*'s byte contents.

    Reads in 64 KB chunks so large files (scanned PDFs, high-res TIFFs) are
    hashed without loading the entire file into memory.
    """
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict:
    """Load the manifest from disk.

    Returns an empty dict if the manifest file does not yet exist, so callers
    do not need to check for the file's existence before using the manifest.
    """
    if not MANIFEST_FILE.exists():
        return {}
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest: dict) -> None:
    """Write *manifest* back to disk, pretty-printed for human readability."""
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def is_processed(filepath: Path, manifest: dict) -> bool:
    """Return True if *filepath*'s content hash is already in *manifest*."""
    return file_hash(filepath) in manifest


def mark_processed(filepath: Path, wiki_page: Path, manifest: dict) -> dict:
    """Record *filepath* as processed and return the updated manifest.

    Does **not** write to disk — call :func:`save_manifest` afterwards.
    The separation allows callers to mark several files before writing once.
    """
    key = file_hash(filepath)
    manifest[key] = {
        "filename":  filepath.name,
        "path":      str(filepath),
        "processed": datetime.now(timezone.utc).isoformat(),
        "wiki_page": str(wiki_page),
    }
    return manifest


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    manifest = load_manifest()
    if not manifest:
        print("Manifest is empty — no files have been processed yet.")
        sys.exit(0)

    print(f"{'Filename':<45}  {'Processed (UTC)':<26}  Wiki page")
    print("-" * 110)
    for entry in manifest.values():
        print(
            f"{entry['filename']:<45}  "
            f"{entry['processed']:<26}  "
            f"{entry['wiki_page']}"
        )
