#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Mnemotron Wiki — synthesize_links.py
# Originally developed by Patrick R. Wallace, Hamilton College LITS.
# Licensed under the GNU General Public License v3 or later.
# See <https://www.gnu.org/licenses/gpl-3.0.html>.

"""
synthesize_links.py — Keyword-based topic linking for source pages.

Reads each wiki/sources/*.md page, matches its content against the TOPIC_MAP
below, and appends a ## Related Topics section to pages that don't already
have one.

This is the mechanical first step of the synthesis pipeline — it answers
"which topics does this source touch?" automatically, so that the manual
synthesis pass (reading sources, extracting Key Points, and updating topic
Sources tables) can focus on analysis rather than bookkeeping.

Customization
-------------
Edit TOPIC_MAP below for your corpus before running.  Each entry needs:
  - "slug":      the filename stem of the topic page (without .md)
  - "title":     the display title used in the Related Topics link
  - "path":      relative path from wiki/sources/ to the topic page
  - "keywords":  list of case-insensitive strings to search for in source content
  - "threshold": minimum number of keyword matches required to link the topic
                 (set to 0 to match every source page unconditionally)

Prefer compound phrases over single words for high-frequency terms to avoid
false positives (e.g., "student assembly" rather than "assembly").

Usage:
    python synthesize_links.py [--dry-run] [--limit N]
"""

import argparse
import re
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent
SOURCES_DIR = WIKI_ROOT / "wiki" / "sources"
TOPICS_DIR = WIKI_ROOT / "wiki" / "topics"

# ---------------------------------------------------------------------------
# TOPIC_MAP — customize this for your corpus
# ---------------------------------------------------------------------------
# Each entry: { "slug", "title", "path", "keywords", "threshold" }
# threshold=0 matches every source page unconditionally (useful for an
# overview/collection topic that should link to all sources).

TOPIC_MAP = [
    # Example: an overview topic that links to every source page.
    # Replace with a slug, title, and path that match your actual topic file.
    {
        "slug": "corpus-overview",
        "title": "Corpus Overview",
        "path": "../topics/corpus-overview.md",
        "keywords": [],
        "threshold": 0,   # always matched
    },

    # Example: a thematic topic matched by keywords.
    # Adjust keywords and threshold to suit your corpus's vocabulary.
    {
        "slug": "topic-a",
        "title": "Topic A",
        "path": "../topics/topic-a.md",
        "keywords": [
            "keyword-one", "keyword-two", "keyword-three",
        ],
        "threshold": 2,   # require at least 2 keyword matches
    },

    # Example: a topic with a single unambiguous keyword.
    {
        "slug": "topic-b",
        "title": "Topic B",
        "path": "../topics/topic-b.md",
        "keywords": [
            "unambiguous phrase", "another distinctive term",
        ],
        "threshold": 1,   # one match is enough for high-signal keywords
    },
]

RELATED_TOPICS_SECTION_SEP = "\n\n---\n\n"


# ---------------------------------------------------------------------------
# Matching and page-building logic
# ---------------------------------------------------------------------------

def match_topics(text: str) -> list:
    """Return list of TOPIC_MAP entries whose keywords match the text."""
    lower = text.lower()
    matched = []
    for topic in TOPIC_MAP:
        if topic["threshold"] == 0:
            matched.append(topic)
            continue
        count = sum(1 for kw in topic["keywords"] if kw in lower)
        if count >= topic["threshold"]:
            matched.append(topic)
    return matched


def has_related_topics(content: str) -> bool:
    return "## Related Topics" in content


def build_related_topics_section(topics: list) -> str:
    lines = ["## Related Topics", ""]
    for t in topics:
        lines.append(f"- [{t['title']}]({t['path']})")
    return "\n".join(lines) + "\n"


def process_file(path: Path, dry_run: bool) -> tuple:
    """Add Related Topics section if missing. Return (modified, message)."""
    content = path.read_text(encoding="utf-8")

    if has_related_topics(content):
        return False, "already has Related Topics"

    # Extract content section for keyword matching (avoid matching frontmatter)
    content_match = re.search(r"## Content\n\n(.*)", content, re.DOTALL)
    search_text = content_match.group(1) if content_match else content

    matched = match_topics(search_text)
    if not matched:
        return False, "no topics matched"

    section = RELATED_TOPICS_SECTION_SEP + build_related_topics_section(matched)
    if not dry_run:
        path.write_text(content.rstrip() + section, encoding="utf-8")

    return True, f"{len(matched)} topic(s): {', '.join(t['slug'] for t in matched)}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    pages = sorted(SOURCES_DIR.glob("*.md"))

    if args.limit:
        pages = pages[: args.limit]

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Processing {len(pages)} source pages...")

    modified, skipped = 0, 0
    topic_counts: dict = {}

    for i, page in enumerate(pages, 1):
        changed, msg = process_file(page, args.dry_run)
        if i % 100 == 0 or i == len(pages):
            print(f"  [{i:4d}/{len(pages)}] {page.name}: {msg}")
        if changed:
            modified += 1
            for slug in msg.split(": ", 1)[-1].split(", "):
                topic_counts[slug] = topic_counts.get(slug, 0) + 1
        else:
            skipped += 1

    print(f"\n{prefix}Done: {modified} modified, {skipped} skipped.")
    if topic_counts:
        print("Topic match counts:")
        for slug, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
            print(f"  {count:4d}  {slug}")


if __name__ == "__main__":
    main()
