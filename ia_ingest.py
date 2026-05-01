#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Mnemotron Wiki — ia_ingest.py
# Originally developed by Patrick R. Wallace, Hamilton College LITS.
# Licensed under the GNU General Public License v3 or later.
# See <https://www.gnu.org/licenses/gpl-3.0.html>.

"""
ia_ingest.py — Fetch and ingest documents from Internet Archive.

Reads a CSV of IA identifiers and ingests each item into wiki/sources/:

  1. Skip identifiers already recorded in ingest/ia-sources/processed.json.
  2. Fetch IA metadata via `ia metadata`; verify mediatype == "texts".
  3. Download *_djvu.txt — IA's pre-built Tesseract OCR text (fast, ~100–400 KB).
  4. Evaluate OCR quality:
       - PASS (≥ IA_OCR_MIN_WORDS words AND ≥ IA_OCR_MIN_ALPHA_RATIO alpha ratio)
         → write source page directly from the djvu text (ocr_method: ia-tesseract).
       - FAIL (too few words OR too noisy)
         → fall back to the original image PDF.
  5. PDF fallback:
       a. Download <identifier>.pdf from IA (slow, may be 50–100 MB).
       b. Try pdfminer text-layer extraction (fast; handles PDFs with embedded text).
       c. If the PDF is a scan (no usable text layer) → run the local OCR pipeline:
            - Tesseract first (per-page, with quality gating).
            - Claude Vision fallback for pages Tesseract cannot read.
  6. Write wiki/sources/<slug>.md and record the identifier in processed.json.

Processed log
-------------
ingest/ia-sources/processed.json tracks all IA identifiers that have been
successfully ingested. It is separate from the content-hash-based .manifest.json
because IA items are identified by their item identifier, not a local file hash.

Customization
-------------
Source page formatting is handled by _build_page() in batch_ingest.py.  By
default, _build_page() falls back to a generic page generator.  To get
corpus-specific formatting for your IA items (e.g., parsing a date from the
identifier, deriving a structured title), add a matching branch to _build_page()
and update the virtual_path construction in _write_source_page() to trigger it.

Usage
-----
    python ia_ingest.py                      # process all pending identifiers
    python ia_ingest.py --dry-run            # plan without downloading/writing
    python ia_ingest.py --limit 10           # process at most 10 identifiers
    python ia_ingest.py --csv path/to.csv    # use an alternate CSV file
    python ia_ingest.py --verbose            # per-step progress to stderr
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Optional

WIKI_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(WIKI_ROOT))

from scripts.config import SOURCES_DIR
from batch_ingest import make_slug, _build_page

TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# OCR quality thresholds for a full-document djvu.txt.  These are more lenient
# than the per-page thresholds in ocr.py because we are checking an entire
# document, not a single page.
IA_OCR_MIN_WORDS = 100        # fewer words → minimal/failed OCR → PDF fallback
IA_OCR_MIN_ALPHA_RATIO = 0.40  # below this → excessively noisy → PDF fallback

# Polite delay between IA requests to avoid hammering the servers.
IA_DOWNLOAD_DELAY = 1.0  # seconds

# Timeout values (seconds) for ia download subprocess calls.
DJVU_DOWNLOAD_TIMEOUT = 120   # djvu.txt files are small
PDF_DOWNLOAD_TIMEOUT  = 600   # original image PDFs can be 50–100 MB

# Paths
IA_SOURCES_DIR = WIKI_ROOT / "ingest" / "ia-sources"
PROCESSED_LOG  = IA_SOURCES_DIR / "processed.json"
DEFAULT_CSV    = IA_SOURCES_DIR / "search.csv"


# ---------------------------------------------------------------------------
# Processed log — IA-specific tracking
# ---------------------------------------------------------------------------

def _load_log() -> dict:
    """Load the IA processed log.  Returns {} if the file does not yet exist."""
    if not PROCESSED_LOG.exists():
        return {}
    with open(PROCESSED_LOG, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_log(log: dict) -> None:
    """Persist the IA processed log to disk."""
    PROCESSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------

def _read_identifiers(csv_path: Path) -> list:
    """Read IA identifiers from the first column of a CSV file.

    Strips surrounding quotes and whitespace.  Skips a header row whose first
    cell is "identifier" (case-insensitive).  Skips blank rows.
    """
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        return []

    first_cell = rows[0][0].strip().strip('"').lower()
    start = 1 if first_cell == "identifier" else 0

    return [
        row[0].strip().strip('"')
        for row in rows[start:]
        if row and row[0].strip()
    ]


# ---------------------------------------------------------------------------
# IA CLI wrappers
# ---------------------------------------------------------------------------

def _ia_run(args, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run an `ia` CLI command.  Captures stdout/stderr.  Never raises."""
    try:
        return subprocess.run(
            ["ia"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "The `ia` CLI tool is not installed or not on PATH.  "
            "Install it with: pip install internetarchive"
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ia command timed out after {timeout}s: {exc}")


def _ia_metadata(identifier: str) -> Optional[dict]:
    """Fetch IA item metadata as a parsed dict.  Returns None on any error."""
    try:
        result = _ia_run(["metadata", identifier], timeout=30)
    except RuntimeError as exc:
        print(f"  [ia] {exc}", file=sys.stderr)
        return None

    if result.returncode != 0:
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _ia_files(meta: dict) -> list:
    """Return the files list from IA metadata, or []."""
    return meta.get("files", [])


def _ia_has_file(meta: dict, suffix: str) -> bool:
    """Return True if any file in the item has the given suffix."""
    return any(f.get("name", "").endswith(suffix) for f in _ia_files(meta))


def _ia_original_pdf_name(meta: dict, identifier: str) -> str:
    """Return the filename of the original image PDF for this IA item.

    Prefers a file whose IA format is "Image Container PDF".  Falls back to
    <identifier>.pdf (the common IA naming convention).
    """
    for f in _ia_files(meta):
        if (
            f.get("name", "") == f"{identifier}.pdf"
            and f.get("format") == "Image Container PDF"
        ):
            return f["name"]
    return f"{identifier}.pdf"


def _ia_download(
    identifier: str,
    glob: str,
    dest_dir: Path,
    timeout: int = DJVU_DOWNLOAD_TIMEOUT,
    verbose: bool = False,
) -> list:
    """Download files matching *glob* from IA item *identifier* into *dest_dir*.

    Uses --no-directories so files land directly in dest_dir rather than in a
    <dest_dir>/<identifier>/ subdirectory.  Falls back to rglob if older ia
    versions create a subdirectory anyway.

    Returns a list of Path objects for every new file found after the download.
    """
    if verbose:
        print(f"    ia download {identifier!r} --glob={glob!r}", file=sys.stderr)

    before = {p for p in dest_dir.rglob("*") if p.is_file()}

    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = _ia_run(
            [
                "download", identifier,
                f"--glob={glob}",
                f"--destdir={dest_dir}",
                "--no-directories",
            ],
            timeout=timeout,
        )
        if verbose and result.returncode != 0:
            print(f"    ia download exit {result.returncode}", file=sys.stderr)
    except RuntimeError as exc:
        print(f"  [ia] {exc}", file=sys.stderr)
        return []

    after = {p for p in dest_dir.rglob("*") if p.is_file()}
    return list(after - before)


# ---------------------------------------------------------------------------
# OCR quality evaluation for full-document djvu.txt
# ---------------------------------------------------------------------------

def _djvu_quality(text: str) -> tuple:
    """Return (is_ok: bool, reason: str) for a full-document djvu.txt.

    Two independent checks mirror those in ocr.py but with thresholds tuned
    for a complete document rather than a single page:

    1. Word count ≥ IA_OCR_MIN_WORDS — catches minimal/empty OCR output.
    2. Alpha ratio ≥ IA_OCR_MIN_ALPHA_RATIO — catches excessively noisy OCR
       where the majority of non-whitespace characters are not alphabetic.
    """
    words = text.split()
    word_count = len(words)

    if word_count < IA_OCR_MIN_WORDS:
        return False, f"too few words ({word_count} < {IA_OCR_MIN_WORDS})"

    non_ws = re.sub(r"\s", "", text)
    if not non_ws:
        return False, "no non-whitespace content"

    alpha_ratio = sum(c.isalpha() for c in non_ws) / len(non_ws)
    if alpha_ratio < IA_OCR_MIN_ALPHA_RATIO:
        return (
            False,
            f"alpha ratio too low ({alpha_ratio:.2f} < {IA_OCR_MIN_ALPHA_RATIO})",
        )

    return True, f"{word_count:,} words, alpha={alpha_ratio:.2f}"


# ---------------------------------------------------------------------------
# Source page builder
# ---------------------------------------------------------------------------

def _write_source_page(
    identifier: str,
    text: str,
    ocr_method: str,
    sources_dir: Path,
    existing_slugs: set,
    dry_run: bool,
) -> str:
    """Build and write a wiki/sources/ page for *identifier*.

    Derives the slug from the IA identifier using the shared slug-generation
    convention.  Source page content is produced by _build_page() from
    batch_ingest.py — add corpus-specific branches there to customize the
    output for your IA item naming scheme.

    The virtual_path passed to _build_page() controls dispatch.  By default
    it uses "<identifier>.txt" which triggers the generic document page
    generator.  If your corpus identifiers follow a pattern handled by a custom
    branch in _build_page(), adjust the virtual_path extension accordingly.

    Returns the path of the wiki page relative to WIKI_ROOT (for logging).
    """
    virtual_path = Path(f"{identifier}.txt")
    slug = make_slug(virtual_path, existing_slugs)

    page_content = _build_page(virtual_path, text, slug, {})

    # Patch ocr_method: _build_page generators write "direct" by default.
    page_content = page_content.replace(
        'ocr_method: "direct"',
        f'ocr_method: "{ocr_method}"',
        1,
    )
    # Patch original_file: record the IA identifier rather than a local filename.
    page_content = page_content.replace(
        f'original_file: "{virtual_path.name}"',
        f'original_file: "ia:{identifier}"',
        1,
    )

    wiki_page = sources_dir / f"{slug}.md"

    if not dry_run:
        wiki_page.write_text(page_content, encoding="utf-8")

    return str(wiki_page.relative_to(WIKI_ROOT))


# ---------------------------------------------------------------------------
# djvu.txt path
# ---------------------------------------------------------------------------

def _try_djvu(
    identifier: str,
    meta: dict,
    sources_dir: Path,
    existing_slugs: set,
    dry_run: bool,
    verbose: bool,
) -> tuple:
    """Attempt to ingest via IA's pre-built djvu.txt OCR text.

    Returns (success: bool, wiki_page: str, reason: str).
    On success, wiki_page is the relative path of the created source page.
    On failure, wiki_page is empty and reason explains why.
    """
    if not _ia_has_file(meta, "_djvu.txt"):
        return False, "", "no *_djvu.txt in item"

    with tempfile.TemporaryDirectory(prefix="ia_djvu_") as tmp:
        tmp_dir = Path(tmp)

        if dry_run:
            return True, "(dry-run)", "would download djvu.txt"

        if verbose:
            print("  Downloading *_djvu.txt …", file=sys.stderr)

        downloaded = _ia_download(
            identifier, "*_djvu.txt", tmp_dir,
            timeout=DJVU_DOWNLOAD_TIMEOUT, verbose=verbose,
        )

        if not downloaded:
            return False, "", "djvu.txt download produced no files"

        djvu_path = downloaded[0]
        text = djvu_path.read_text(encoding="utf-8", errors="replace")
        ok, reason = _djvu_quality(text)

        if verbose:
            print(f"  djvu quality: {'OK' if ok else 'POOR'} — {reason}", file=sys.stderr)

        if not ok:
            return False, "", f"djvu quality insufficient: {reason}"

        wiki_page = _write_source_page(
            identifier, text, "ia-tesseract",
            sources_dir, existing_slugs, dry_run,
        )
        return True, wiki_page, reason


# ---------------------------------------------------------------------------
# PDF fallback path
# ---------------------------------------------------------------------------

def _try_pdf(
    identifier: str,
    meta: dict,
    sources_dir: Path,
    existing_slugs: set,
    dry_run: bool,
    verbose: bool,
) -> tuple:
    """Ingest via the original image PDF when djvu.txt is absent or too noisy.

    Steps:
      1. Download the original image PDF from IA.
      2. Try pdfminer text-layer extraction.
      3. If no usable text layer → route through the local OCR pipeline
         (Tesseract per-page with Claude Vision fallback).

    Returns (success: bool, wiki_page: str, ocr_method: str | reason).
    """
    from scripts.extract_text import extract_text
    from scripts.ocr import ocr_file

    pdf_name = _ia_original_pdf_name(meta, identifier)

    if dry_run:
        return True, "(dry-run)", f"would download {pdf_name} then OCR"

    with tempfile.TemporaryDirectory(prefix="ia_pdf_") as tmp:
        tmp_dir = Path(tmp)

        if verbose:
            print(f"  Downloading {pdf_name} …", file=sys.stderr)

        downloaded = _ia_download(
            identifier, pdf_name, tmp_dir,
            timeout=PDF_DOWNLOAD_TIMEOUT, verbose=verbose,
        )

        if not downloaded:
            return False, "", f"PDF download produced no files (tried {pdf_name!r})"

        pdf_path = downloaded[0]

        if verbose:
            print("  Trying pdfminer text extraction …", file=sys.stderr)

        ext_result = extract_text(pdf_path)

        if ext_result["error"]:
            return False, "", f"extract_text error: {ext_result['error']}"

        if not ext_result["is_scan"] and ext_result["text"].strip():
            if verbose:
                print("  pdfminer: text layer found.", file=sys.stderr)
            wiki_page = _write_source_page(
                identifier, ext_result["text"], "pdfminer",
                sources_dir, existing_slugs, dry_run,
            )
            return True, wiki_page, "pdfminer"

        if verbose:
            print("  Scanned PDF — running local OCR pipeline …", file=sys.stderr)

        ocr_result = ocr_file(pdf_path, hint="print")

        if ocr_result["error"]:
            return False, "", f"OCR pipeline error: {ocr_result['error']}"

        ocr_method = ocr_result["method"]
        wiki_page = _write_source_page(
            identifier, ocr_result["text"], ocr_method,
            sources_dir, existing_slugs, dry_run,
        )
        return True, wiki_page, ocr_method


# ---------------------------------------------------------------------------
# Per-identifier orchestrator
# ---------------------------------------------------------------------------

def _process_one(
    identifier: str,
    sources_dir: Path,
    existing_slugs: set,
    processed_log: dict,
    dry_run: bool,
    verbose: bool,
) -> tuple:
    """Process one IA identifier end-to-end.

    Returns (success: bool, message: str).
    On success the processed_log is updated and saved to disk.
    """
    meta = _ia_metadata(identifier)
    if meta is None:
        return False, "metadata fetch failed (network error or unknown identifier)"

    item_meta = meta.get("metadata", {})
    mediatype = item_meta.get("mediatype", "")
    if mediatype != "texts":
        return False, f"mediatype={mediatype!r} — not a texts item; skipped"

    ok, wiki_page, detail = _try_djvu(
        identifier, meta, sources_dir, existing_slugs, dry_run, verbose
    )

    if ok:
        if not dry_run:
            processed_log[identifier] = {
                "wiki_page": wiki_page,
                "ingested": TODAY,
                "ocr_source": "ia-tesseract",
            }
            _save_log(processed_log)
        return True, f"djvu → {wiki_page}  ({detail})"

    if verbose:
        print(f"  djvu path skipped: {detail} — falling back to PDF.", file=sys.stderr)

    ok, wiki_page, ocr_method = _try_pdf(
        identifier, meta, sources_dir, existing_slugs, dry_run, verbose
    )

    if not ok:
        return False, f"PDF fallback failed: {ocr_method}"

    if not dry_run:
        processed_log[identifier] = {
            "wiki_page": wiki_page,
            "ingested": TODAY,
            "ocr_source": ocr_method,
        }
        _save_log(processed_log)

    return True, f"pdf/{ocr_method} → {wiki_page}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        metavar="PATH",
        help=f"CSV of IA identifiers to process (default: {DEFAULT_CSV.relative_to(WIKI_ROOT)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Fetch metadata and evaluate which path would be taken, "
            "but do not download files or write source pages"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process at most N pending identifiers (0 = no limit)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-step progress to stderr",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    all_identifiers = _read_identifiers(args.csv)
    if not all_identifiers:
        print("No identifiers found in CSV.")
        return

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    processed_log = _load_log()
    existing_slugs: set = {p.stem for p in SOURCES_DIR.glob("*.md")}

    pending_all = [i for i in all_identifiers if i not in processed_log]
    already_done = len(all_identifiers) - len(pending_all)

    if not pending_all:
        print(
            f"All {len(all_identifiers)} identifier(s) in {args.csv.name} "
            "are already processed."
        )
        return

    if args.limit:
        pending = pending_all[: args.limit]
    else:
        pending = pending_all

    prefix = "[DRY RUN] " if args.dry_run else ""
    if already_done:
        print(f"{prefix}{already_done} identifier(s) already done; skipped.")
    remaining_after = len(pending_all) - len(pending)
    if remaining_after:
        print(f"{prefix}{remaining_after} more pending after this run (increase --limit to process more).")
    print(f"{prefix}Processing {len(pending)} identifier(s) from {args.csv.name} …")

    success_count = 0
    failed = []

    for i, identifier in enumerate(pending, 1):
        print(f"  [{i:4d}/{len(pending)}] {identifier}", end="  ", flush=True)

        try:
            ok, msg = _process_one(
                identifier,
                SOURCES_DIR,
                existing_slugs,
                processed_log,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except Exception as exc:
            ok, msg = False, f"unexpected error: {type(exc).__name__}: {exc}"

        marker = "+" if ok else "!"
        print(f"{marker}  {msg}")

        if ok:
            success_count += 1
        else:
            failed.append((identifier, msg))

        if i < len(pending):
            time.sleep(IA_DOWNLOAD_DELAY)

    print(f"\n{prefix}Done: {success_count} ingested, {len(failed)} failed.")
    if failed:
        print("Failures:")
        for ident, err in failed:
            print(f"  {ident}: {err}")
    if not args.dry_run and success_count:
        print(
            f"\nNext: run 'git add wiki/sources/ ingest/ia-sources/processed.json && "
            f"git commit' to save the new source pages."
        )


if __name__ == "__main__":
    main()
