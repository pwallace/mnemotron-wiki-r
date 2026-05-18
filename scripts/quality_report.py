#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Mnemotron Wiki — scripts/quality_report.py
# Originally developed by Patrick R. Wallace, Hamilton College LITS.
# Licensed under the GNU General Public License v3 or later.
# See <https://www.gnu.org/licenses/gpl-3.0.html>.

"""
quality_report.py — OCR quality summary across wiki/sources/.

Reads all source page frontmatter and ## Notes sections to produce a summary:

  - Source count by ocr_method (tesseract, claude, tesseract+claude,
    ia-tesseract, pdfminer, direct, unknown)
  - Source count by document type (scan-print, scan-handwritten, pdf, web,
    notes, data, unknown)
  - Pages flagged "Manual review recommended: yes" in their ## Notes section
  - Pages with no ocr_method field (pre-standard format)
  - Publication breakdown (derived from the "publication" frontmatter field,
    or from tags using PUBLICATION_TAGS below — customize for your corpus)

Usage:
    python scripts/quality_report.py                  # full summary
    python scripts/quality_report.py --flagged        # list review-flagged pages
    python scripts/quality_report.py --method M       # list pages by OCR method
    python scripts/quality_report.py --publication P  # list pages by publication
    python scripts/quality_report.py --no-ocr         # list pages missing ocr_method

Customization
-------------
Set PUBLICATION_TAGS to a list of (label, tag) tuples to map tag names to
human-readable publication labels.  Tags are checked in order; first match
wins.  Pages with no matching tag fall back to "Other".

Example:
    PUBLICATION_TAGS = [
        ("The Daily",    "daily"),
        ("Annual Report", "annual-report"),
    ]

Alternatively, source pages with a "publication:" frontmatter field will use
that value directly (takes priority over PUBLICATION_TAGS).
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WIKI_ROOT   = Path(__file__).resolve().parent.parent
SOURCES_DIR = WIKI_ROOT / "wiki" / "sources"

# ---------------------------------------------------------------------------
# Corpus-specific configuration — customize for your project
# ---------------------------------------------------------------------------

# Map tag names → publication labels.  Add entries here to populate the
# "Publication Breakdown" section of the quality report.  Leave empty to
# report all pages as "Other".
PUBLICATION_TAGS: list = [
    # ("Publication Label", "tag-name"),
]


# ---------------------------------------------------------------------------
# Frontmatter / Notes parsing
# ---------------------------------------------------------------------------

def _parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter block into a flat dict of string values."""
    m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        kv = re.match(r'^(\w[\w_-]*):\s*"?([^"#\n]*)"?\s*$', line)
        if kv:
            fm[kv.group(1)] = kv.group(2).strip()
    return fm


def _parse_tags(content: str) -> list:
    """Extract tag list from frontmatter (multi-line YAML list format)."""
    m = re.search(r"^tags:\n((?:\s+-[^\n]+\n)*)", content, re.MULTILINE)
    if not m:
        return []
    return [re.sub(r"^\s+-\s*", "", line).strip().strip('"')
            for line in m.group(1).splitlines() if line.strip()]


def _parse_review_flag(content: str) -> bool:
    """Return True if the ## Notes section contains a manual-review recommendation."""
    m = re.search(r"## Notes\n\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if not m:
        return False
    notes = m.group(1).lower()
    return bool(re.search(r"manual review recommended:\s*yes", notes))


def _publication_label(fm: dict, tags: list) -> str:
    """Derive publication label from frontmatter or tags.

    Checks in order:
    1. "publication" frontmatter field (explicit, most reliable)
    2. PUBLICATION_TAGS mapping (tag → label)
    3. Falls back to "Other"
    """
    pub = fm.get("publication", "").strip()
    if pub:
        return pub
    tag_set = set(tags)
    for label, marker in PUBLICATION_TAGS:
        if marker in tag_set:
            return label
    return "Other"


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

class SourceStat:
    __slots__ = ("path", "title", "ocr_method", "doc_type", "publication",
                 "review_flagged", "original_file")

    def __init__(self, path, title, ocr_method, doc_type, publication,
                 review_flagged, original_file):
        self.path = path
        self.title = title
        self.ocr_method = ocr_method
        self.doc_type = doc_type
        self.publication = publication
        self.review_flagged = review_flagged
        self.original_file = original_file


def collect_stats(sources_dir: Path) -> list:
    """Read all source pages and return a list of SourceStat objects."""
    stats = []
    for page in sorted(sources_dir.glob("*.md")):
        try:
            content = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = _parse_frontmatter(content)
        tags = _parse_tags(content)
        stats.append(SourceStat(
            path=page,
            title=fm.get("title", page.stem),
            ocr_method=fm.get("ocr_method", ""),
            doc_type=fm.get("type", ""),
            publication=_publication_label(fm, tags),
            review_flagged=_parse_review_flag(content),
            original_file=fm.get("original_file", ""),
        ))
    return stats


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _bar(n: int, total: int, width: int = 30) -> str:
    filled = round(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}%" if total else "—"


def print_summary(stats: list) -> None:
    total = len(stats)
    print(f"\n{'='*60}")
    print(f"  WIKI OCR & QUALITY REPORT")
    print(f"  {total:,} source pages in wiki/sources/")
    print(f"{'='*60}\n")

    # --- OCR methods ---
    method_counts = Counter(s.ocr_method or "unknown" for s in stats)
    print("OCR Method Breakdown")
    print(f"  {'Method':<22}  {'Count':>6}  {'%':>6}  {'Distribution'}")
    print(f"  {'-'*22}  {'-'*6}  {'-'*6}  {'-'*30}")
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        label = method if method else "(none)"
        print(f"  {label:<22}  {count:>6,}  {_pct(count,total):>6}  "
              f"{_bar(count, total)}")
    print()

    # --- Document types ---
    type_counts = Counter(s.doc_type or "unknown" for s in stats)
    print("Document Type Breakdown")
    print(f"  {'Type':<22}  {'Count':>6}  {'%':>6}")
    print(f"  {'-'*22}  {'-'*6}  {'-'*6}")
    for dtype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        label = dtype if dtype else "(none)"
        print(f"  {label:<22}  {count:>6,}  {_pct(count,total):>6}")
    print()

    # --- Publication breakdown ---
    pub_counts = Counter(s.publication for s in stats)
    print("Publication Breakdown")
    print(f"  {'Publication':<24}  {'Count':>6}  {'%':>6}")
    print(f"  {'-'*24}  {'-'*6}  {'-'*6}")
    for pub, count in sorted(pub_counts.items(), key=lambda x: -x[1]):
        print(f"  {pub:<24}  {count:>6,}  {_pct(count,total):>6}")
    print()

    # --- OCR method × publication cross-tab ---
    cross: dict = defaultdict(Counter)
    for s in stats:
        cross[s.publication][s.ocr_method or "unknown"] += 1
    all_methods = sorted({s.ocr_method or "unknown" for s in stats})
    header_methods = all_methods[:6]  # cap display at 6 columns
    col_w = 8
    header = "  " + f"{'Publication':<24}"
    for m in header_methods:
        header += f"  {m[:col_w]:>{col_w}}"
    print("OCR Method by Publication")
    print(header)
    print("  " + "-"*24 + ("  " + "-"*col_w) * len(header_methods))
    for pub in sorted(cross, key=lambda p: -pub_counts[p]):
        row = f"  {pub:<24}"
        for m in header_methods:
            row += f"  {cross[pub].get(m, 0):>{col_w}}"
        print(row)
    print()

    # --- Review flags ---
    flagged = [s for s in stats if s.review_flagged]
    no_method = [s for s in stats if not s.ocr_method]
    print(f"Quality Flags")
    print(f"  Pages flagged for manual review:  {len(flagged):,} "
          f"({_pct(len(flagged), total)})")
    print(f"  Pages with no ocr_method field:   {len(no_method):,} "
          f"({_pct(len(no_method), total)})")
    print()


def print_flagged(stats: list) -> None:
    flagged = [s for s in stats if s.review_flagged]
    if not flagged:
        print("No pages flagged for manual review.")
        return
    print(f"\n{len(flagged)} page(s) flagged for manual review:\n")
    for s in sorted(flagged, key=lambda x: x.path.name):
        print(f"  {s.path.name}")
        print(f"    title:       {s.title}")
        print(f"    ocr_method:  {s.ocr_method or '(none)'}")
        print(f"    publication: {s.publication}")
        print()


def print_by_method(stats: list, method: str) -> None:
    matching = [s for s in stats
                if (s.ocr_method or "").lower() == method.lower()]
    if not matching:
        print(f"No pages with ocr_method='{method}'.")
        return
    print(f"\n{len(matching)} page(s) with ocr_method='{method}':\n")
    for s in sorted(matching, key=lambda x: x.path.name):
        print(f"  {s.path.name}  —  {s.title}")


def print_by_publication(stats: list, pub: str) -> None:
    matching = [s for s in stats
                if s.publication.lower() == pub.lower()]
    if not matching:
        print(f"No pages with publication='{pub}'.")
        return
    print(f"\n{len(matching)} page(s) for '{pub}':\n")
    for s in sorted(matching, key=lambda x: x.path.name):
        flag = " [REVIEW]" if s.review_flagged else ""
        print(f"  {s.path.name}  ({s.ocr_method or 'no-method'}){flag}")


def print_no_ocr(stats: list) -> None:
    missing = [s for s in stats if not s.ocr_method]
    if not missing:
        print("All source pages have an ocr_method field.")
        return
    print(f"\n{len(missing)} page(s) with no ocr_method field:\n")
    for s in sorted(missing, key=lambda x: x.path.name):
        print(f"  {s.path.name}  —  {s.title}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--flagged", action="store_true",
                        help="List pages flagged for manual review")
    parser.add_argument("--method", metavar="M",
                        help="List pages with a specific ocr_method value")
    parser.add_argument("--publication", metavar="P",
                        help="List pages for a specific publication")
    parser.add_argument("--no-ocr", dest="no_ocr", action="store_true",
                        help="List pages missing an ocr_method field")
    args = parser.parse_args()

    print("Reading source pages…", end=" ", flush=True)
    stats = collect_stats(SOURCES_DIR)
    print(f"{len(stats):,} pages read.")

    if args.flagged:
        print_flagged(stats)
    elif args.method:
        print_by_method(stats, args.method)
    elif args.publication:
        print_by_publication(stats, args.publication)
    elif args.no_ocr:
        print_no_ocr(stats)
    else:
        print_summary(stats)


if __name__ == "__main__":
    main()
