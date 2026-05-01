#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Mnemotron Wiki — batch_ingest.py
# Originally developed by Patrick R. Wallace, Hamilton College LITS.
# Licensed under the GNU General Public License v3 or later.
# See <https://www.gnu.org/licenses/gpl-3.0.html>.

"""
batch_ingest.py — Bulk ingest runner for this wiki instance.

Processes all unprocessed files in ingest/ and creates wiki/sources/ pages.
Does NOT delete original files.  Saves manifest after each file so a partial
run can be resumed safely.

Usage:
    python batch_ingest.py              # process all pending files
    python batch_ingest.py --dry-run    # plan without writing
    python batch_ingest.py --limit 10   # process at most 10 files

Customization
-------------
This script ships with generic source-page generators suitable for any corpus.
To add corpus-specific formatting (e.g., parsing a date from a newspaper
filename, deriving a structured title from a document identifier), define a
custom page-generator function and add a matching branch to _build_page().
See the comments in _build_page() for the pattern to follow.
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(WIKI_ROOT))

from scripts.extract_text import extract_text
from scripts.manifest import load_manifest, mark_processed, save_manifest
from scripts.check_ingest import get_ingest_files

TODAY = date.today().isoformat()

# For large CSVs and text files, include only this many rows/lines as preview.
CSV_PREVIEW_ROWS = 25
TXT_PREVIEW_LINES = 100


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def make_slug(filepath: Path, existing: set) -> str:
    """Derive a unique URL-safe slug from a filepath stem."""
    stem = filepath.stem.lower().replace("_", "-").replace(".", "-")
    stem = re.sub(r"[^a-z0-9\-]", "", stem)
    stem = re.sub(r"-+", "-", stem).strip("-")
    candidate = stem
    n = 2
    while candidate in existing:
        candidate = f"{stem}-{n}"
        n += 1
    existing.add(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Source page generators
# ---------------------------------------------------------------------------

def _document_page(filepath: Path, text: str, slug: str) -> str:
    """Generic source page for any text document.

    Derives a human-readable title from the filename and writes the full
    extracted text as page content.  Replace or supplement this with
    corpus-specific generators registered in _build_page() below.
    """
    title = filepath.stem.replace("_", " ").replace("-", " ").replace(".", " ").title()
    return (
        f"---\n"
        f'title: "{title}"\n'
        f'type: "notes"\n'
        f'ocr_method: "direct"\n'
        f'ingested: "{TODAY}"\n'
        f'original_file: "{filepath.name}"\n'
        f"tags:\n"
        f"  - source\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"## Source Information\n\n"
        f"Document ingested from `{filepath.name}`.\n\n"
        f"## Content\n\n"
        f"{text}\n"
    )


def _csv_page(filepath: Path, text: str, slug: str, metadata: dict) -> str:
    title = filepath.stem.replace("_", " ").replace("-", " ").title()
    rows = int(metadata.get("row_count", 0))
    cols = metadata.get("columns", [])
    col_str = ", ".join(f"`{c}`" for c in cols) if cols else "unknown"

    lines = text.splitlines()
    if rows > CSV_PREVIEW_ROWS:
        content = "\n".join(lines[: CSV_PREVIEW_ROWS + 1])
        content += f"\n\n*[Preview: first {CSV_PREVIEW_ROWS} of {rows:,} records shown.]*"
    else:
        content = text

    return (
        f"---\n"
        f'title: "{title}"\n'
        f'type: "data"\n'
        f'ocr_method: "direct"\n'
        f'ingested: "{TODAY}"\n'
        f'original_file: "{filepath.name}"\n'
        f"tags:\n"
        f"  - entities\n"
        f"  - structured-data\n"
        f"  - reference\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"## Source Information\n\n"
        f"Structured CSV reference file with {rows:,} records. "
        f"Columns: {col_str}.\n\n"
        f"## Content\n\n"
        f"{content}\n"
    )


def _text_page(filepath: Path, text: str, slug: str) -> str:
    title = filepath.stem.replace("_", " ").replace("-", " ").replace(".", " ").title()
    lines = text.splitlines()

    if len(lines) > TXT_PREVIEW_LINES:
        content = "\n".join(lines[:TXT_PREVIEW_LINES])
        content += f"\n\n*[Preview: first {TXT_PREVIEW_LINES} of {len(lines):,} lines shown.]*"
    else:
        content = text

    return (
        f"---\n"
        f'title: "{title}"\n'
        f'type: "notes"\n'
        f'ocr_method: "direct"\n'
        f'ingested: "{TODAY}"\n'
        f'original_file: "{filepath.name}"\n'
        f"tags:\n"
        f"  - reference\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"## Source Information\n\n"
        f"Plain-text reference file, {len(lines):,} lines.\n\n"
        f"## Content\n\n"
        f"{content}\n"
    )


def _build_page(filepath: Path, text: str, slug: str, metadata: dict) -> str:
    """Dispatch to the appropriate page generator for this file.

    Add corpus-specific branches here.  Example:

        if re.match(r"my-prefix-\\d{4}", filepath.stem):
            return _my_corpus_page(filepath, text, slug)

    Built-in dispatch handles CSVs generically and falls back to _document_page
    for all other types.
    """
    ext = filepath.suffix.lower()
    if ext == ".csv":
        return _csv_page(filepath, text, slug, metadata)
    return _document_page(filepath, text, slug)


# ---------------------------------------------------------------------------
# Single-file ingest
# ---------------------------------------------------------------------------

def _ingest_one(
    filepath: Path,
    sources_dir: Path,
    manifest: dict,
    existing_slugs: set,
    dry_run: bool,
) -> tuple:
    """Ingest one file. Returns (success, message)."""
    result = extract_text(filepath)
    if result["error"]:
        return False, f"extraction error: {result['error']}"
    if result["is_scan"]:
        return False, "scanned PDF — needs ocr.py (skipped)"

    slug = make_slug(filepath, existing_slugs)
    page_content = _build_page(filepath, result["text"], slug, result["metadata"])
    wiki_page = sources_dir / f"{slug}.md"

    if not dry_run:
        wiki_page.write_text(page_content, encoding="utf-8")
        mark_processed(filepath, wiki_page, manifest)
        # Save manifest after every file so a crash loses at most one entry.
        save_manifest(manifest)

    return True, f"-> {slug}.md"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan without writing files")
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Process at most N files (0 = all)",
    )
    args = parser.parse_args()

    sources_dir = WIKI_ROOT / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    files = get_ingest_files()

    if not files:
        print("No unprocessed files found in ingest/.")
        return

    if args.limit:
        files = files[: args.limit]

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Processing {len(files)} file(s)...")

    existing_slugs: set = {p.stem for p in sources_dir.glob("*.md")}
    success, failed = 0, []

    for i, fp in enumerate(files, 1):
        ok, msg = _ingest_one(fp, sources_dir, manifest, existing_slugs, args.dry_run)
        marker = "+" if ok else "!"
        print(f"  [{i:4d}/{len(files)}] {marker} {fp.name}  {msg}")
        if ok:
            success += 1
        else:
            failed.append((fp.name, msg))

    print(f"\n{prefix}Done: {success} ingested, {len(failed)} failed.")
    if failed:
        print("Failures:")
        for name, err in failed:
            print(f"  {name}: {err}")


if __name__ == "__main__":
    main()
