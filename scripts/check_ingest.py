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
check_ingest.py — List files in ingest/ that have not yet been processed.

This script is the first thing Claude runs during each ingest task.  It prints
one absolute filepath per line so Claude can iterate over the list, or so the
output can be piped to other tools.

Files are excluded from the listing when:
  - Their extension is not in SUPPORTED_EXTENSIONS (config.py).
  - Their name starts with "." (hidden files, e.g. .DS_Store).
  - They live under ingest/failed/ (quarantine — never auto-retried).
  - Their MD5 content hash is already in .manifest.json (already processed).

Usage
-----
    python scripts/check_ingest.py            # unprocessed files only (default)
    python scripts/check_ingest.py --all      # all supported files, inc. processed
    python scripts/check_ingest.py --summary  # print counts only, no file paths
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.config import INGEST_DIR, FAILED_INGEST_DIR, SUPPORTED_EXTENSIONS
from scripts.manifest import load_manifest, is_processed


def get_ingest_files(include_processed: bool = False) -> list[Path]:
    """Return a sorted list of files in ingest/ that are ready to process.

    Parameters
    ----------
    include_processed:
        When False (default), return only files whose content hash is absent
        from the manifest.  When True, return all supported non-hidden files
        outside the failed/ quarantine.
    """
    if not INGEST_DIR.exists():
        return []

    # Load the manifest once for all is_processed() calls below.
    manifest = load_manifest()
    results = []

    for filepath in sorted(INGEST_DIR.rglob("*")):
        # Skip directories — we only care about files.
        if not filepath.is_file():
            continue

        # Never scan the quarantine directory; those files are excluded
        # permanently until the user manually acts on them.
        if FAILED_INGEST_DIR in filepath.parents:
            continue

        # Skip formats we cannot process (images, binaries, etc. not in the
        # supported set).
        if filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        # Skip hidden files (e.g. .DS_Store, editor swap files).
        if filepath.name.startswith("."):
            continue

        if include_processed or not is_processed(filepath, manifest):
            results.append(filepath)

    return results


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    show_all     = "--all"     in sys.argv
    summary_only = "--summary" in sys.argv

    unprocessed = get_ingest_files(include_processed=False)
    all_files   = get_ingest_files(include_processed=True)
    processed_count = len(all_files) - len(unprocessed)

    if summary_only:
        print(f"Total files in ingest/:  {len(all_files)}")
        print(f"  Already processed:     {processed_count}")
        print(f"  Awaiting processing:   {len(unprocessed)}")
        sys.exit(0)

    files_to_show = all_files if show_all else unprocessed
    label = "all" if show_all else "unprocessed"

    if not files_to_show:
        print(f"No {label} files found in {INGEST_DIR}")
        sys.exit(0)

    for f in files_to_show:
        print(f)
