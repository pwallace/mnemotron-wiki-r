#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Mnemotron Wiki — synthesize_links.py
# Originally developed by Patrick R. Wallace, Hamilton College LITS.
# Licensed under the GNU General Public License v3 or later.
# See <https://www.gnu.org/licenses/gpl-3.0.html>.

"""
synthesize_links.py — Automatic topic linking for wiki source pages.

Builds a TOPIC_MAP at runtime by reading wiki/topics/, extracts keywords from
each topic's title and Overview section, then appends (or rebuilds) a
## Related Topics section on every source page that matches.

Usage:
    python synthesize_links.py              # add sections to pages that lack them
    python synthesize_links.py --rebuild    # strip and regenerate ALL sections
    python synthesize_links.py --dry-run    # show what would change without writing
    python synthesize_links.py --limit N    # process at most N pages
    python synthesize_links.py --pattern P  # glob pattern for sources (default *.md)
    python synthesize_links.py --show-map   # print auto-generated map and exit

The topic map is regenerated from wiki/topics/ on every run, so it stays in
sync as new topic pages are added without any manual editing of this script.
"""

import argparse
import re
from pathlib import Path

WIKI_ROOT = Path(__file__).resolve().parent
SOURCES_DIR = WIKI_ROOT / "wiki" / "sources"
TOPICS_DIR  = WIKI_ROOT / "wiki" / "topics"

RELATED_TOPICS_SEP = "\n\n---\n\n"

# ---------------------------------------------------------------------------
# Stop-word filter
# ---------------------------------------------------------------------------

# Common English function words excluded from auto-generated keyword lists
# because they are not discriminating identifiers.  Add corpus-specific
# high-frequency terms (names, places, institutions that appear in virtually
# every source) to reduce false-positive matches.
_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "its", "it", "he", "she", "they", "we", "this", "that", "their",
    "has", "have", "had", "not", "all", "also", "which", "who", "when",
    "where", "what", "how", "than", "into", "over", "after", "before",
    "about", "during", "between", "through", "within", "would", "could",
    "each", "such", "more", "most", "some", "many", "first", "last",
})


# ---------------------------------------------------------------------------
# Topic map auto-generation
# ---------------------------------------------------------------------------

def _slug_keywords(slug: str) -> list:
    """Extract meaningful keywords from a topic slug (hyphen-separated words)."""
    words = slug.split("-")
    return [w for w in words if len(w) >= 4 and w not in _STOP_WORDS]


def _title_keywords(title: str) -> list:
    """Extract meaningful single words from a topic title."""
    tokens = re.split(r"[\s\-–—()\[\],:;/]", title)
    result = []
    for w in tokens:
        w = re.sub(r"[^a-zA-Z0-9]", "", w).lower()
        if len(w) >= 4 and w not in _STOP_WORDS and not w.isdigit():
            result.append(w)
    return result


def _extract_overview(content: str) -> str:
    """Return the text of the ## Overview section from a topic page."""
    m = re.search(r"## Overview\n\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    return m.group(1).strip() if m else ""


def _overview_phrases(text: str) -> list:
    """Extract capitalised proper-noun phrases from overview text.

    Looks for runs of two or more Title-Case words (likely proper nouns,
    organisation names, etc.) and lowercases them for keyword matching.
    Single capitalised words shorter than 6 characters are skipped —
    they are usually sentence starts or abbreviations, not specific identifiers.
    """
    results = []

    # Multi-word Title-Case phrases: "Social Committee", "Annual Review", etc.
    phrases = re.findall(r"\b(?:[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)\b", text)
    seen = set()
    for p in phrases:
        p_lower = p.lower()
        if p_lower in seen:
            continue
        seen.add(p_lower)
        words = p_lower.split()
        useful = [w for w in words if w not in _STOP_WORDS and len(w) >= 3]
        if useful:
            results.append(p_lower)

    # Single capitalised words ≥ 6 chars (probably a specific proper noun)
    single_caps = re.findall(r"\b[A-Z][a-zA-Z]{5,}\b", text)
    for w in single_caps:
        w_lower = w.lower()
        if w_lower not in _STOP_WORDS and w_lower not in seen:
            seen.add(w_lower)
            results.append(w_lower)

    return results


def build_topic_map() -> list:
    """Build the TOPIC_MAP at runtime from wiki/topics/ pages.

    For each topic page:
    - Reads the H1 title and Overview section.
    - Extracts keywords from the title, slug, and proper-noun phrases in the
      overview.
    - Sets threshold=1 when most keywords are multi-word phrases (very
      specific); threshold=2 otherwise.

    Returns a list of dicts: {slug, title, path, keywords, threshold}.
    """
    entries = []
    for topic_path in sorted(TOPICS_DIR.glob("*.md")):
        slug = topic_path.stem
        content = topic_path.read_text(encoding="utf-8")

        # Title: prefer the first H1 heading over the slug
        h1 = re.search(r"^# (.+)$", content, re.MULTILINE)
        title = h1.group(1).strip() if h1 else slug.replace("-", " ").title()

        overview = _extract_overview(content)

        kw_set = set()
        kw_set.update(_slug_keywords(slug))
        kw_set.update(_title_keywords(title))
        kw_set.update(_overview_phrases(overview))

        # Keep keywords ≥ 3 chars; cap at 25 to avoid match inflation
        keywords = sorted(w for w in kw_set if len(w) >= 3)[:25]

        # Threshold: if more than a third of keywords are multi-word phrases
        # they are highly specific, so one match is enough.
        compound_count = sum(1 for k in keywords if " " in k)
        threshold = 1 if compound_count > len(keywords) // 3 else 2

        entries.append({
            "slug":      slug,
            "title":     title,
            "path":      f"../topics/{slug}.md",
            "keywords":  keywords,
            "threshold": threshold,
        })

    return entries


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def match_topics(text: str, topic_map: list) -> list:
    """Return topic_map entries whose keywords appear in *text*."""
    lower = text.lower()
    matched = []
    for topic in topic_map:
        if topic["threshold"] == 0:
            matched.append(topic)
            continue
        count = sum(1 for kw in topic["keywords"] if kw in lower)
        if count >= topic["threshold"]:
            matched.append(topic)
    return matched


def build_related_section(topics: list) -> str:
    lines = ["## Related Topics", ""]
    for t in topics:
        lines.append(f"- [{t['title']}]({t['path']})")
    return "\n".join(lines) + "\n"


def strip_related_topics(content: str) -> str:
    """Remove any existing ## Related Topics section (and its separator)."""
    # With separator (the form this script writes)
    stripped = re.sub(r"\n\n---\n\n## Related Topics\b.*$", "", content,
                      flags=re.DOTALL)
    if stripped != content:
        return stripped.rstrip()
    # Without separator (manually added or written by Claude)
    stripped = re.sub(r"\n\n## Related Topics\b.*$", "", content,
                      flags=re.DOTALL)
    return stripped.rstrip()


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def process_file(path: Path, topic_map: list, rebuild: bool,
                 dry_run: bool) -> tuple:
    """Add or rebuild the ## Related Topics section on *path*.

    Returns (modified: bool, message: str).
    """
    content = path.read_text(encoding="utf-8")

    has_section = "## Related Topics" in content
    if has_section and not rebuild:
        return False, "already has Related Topics"

    if rebuild and has_section:
        content = strip_related_topics(content)

    # Match against the ## Content section only (avoid frontmatter noise)
    m = re.search(r"## Content\n\n(.*)", content, re.DOTALL)
    search_text = m.group(1) if m else content

    matched = match_topics(search_text, topic_map)
    if not matched:
        return False, "no topics matched"

    new_content = (content.rstrip()
                   + RELATED_TOPICS_SEP
                   + build_related_section(matched))

    if not dry_run:
        path.write_text(new_content, encoding="utf-8")

    action = "rebuilt" if (rebuild and has_section) else "added"
    return True, f"{action} {len(matched)} topic(s): {', '.join(t['slug'] for t in matched)}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Strip and regenerate Related Topics sections on ALL source pages "
             "(not just those that lack one). Use after adding new topics or to "
             "apply updated keyword extraction to the full corpus.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing any files")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="Process at most N pages (0 = all)")
    parser.add_argument("--pattern", default="*.md", metavar="GLOB",
                        help="Glob pattern for source pages (default: *.md)")
    parser.add_argument("--show-map", action="store_true",
                        help="Print the auto-generated topic map and exit")
    args = parser.parse_args()

    topic_map = build_topic_map()

    if args.show_map:
        print(f"Auto-generated topic map ({len(topic_map)} entries):\n")
        for e in topic_map:
            print(f"  [{e['slug']}]  threshold={e['threshold']}")
            print(f"    keywords: {', '.join(e['keywords'][:10])}"
                  + ("…" if len(e['keywords']) > 10 else ""))
        return

    pages = sorted(SOURCES_DIR.glob(args.pattern))
    if args.limit:
        pages = pages[: args.limit]

    prefix = "[DRY RUN] " if args.dry_run else ""
    mode   = " (rebuild mode)" if args.rebuild else ""
    print(f"{prefix}Processing {len(pages)} source page(s){mode} "
          f"against {len(topic_map)}-topic map…")

    modified, skipped = 0, 0
    topic_counts: dict = {}

    for i, page in enumerate(pages, 1):
        changed, msg = process_file(page, topic_map, args.rebuild, args.dry_run)
        if i % 200 == 0 or i == len(pages):
            print(f"  [{i:5d}/{len(pages)}] {page.name}: {msg}")
        if changed:
            modified += 1
            if ": " in msg:
                for slug in msg.split(": ", 1)[1].split(", "):
                    topic_counts[slug] = topic_counts.get(slug, 0) + 1
        else:
            skipped += 1

    print(f"\n{prefix}Done: {modified} modified, {skipped} skipped.")
    if topic_counts:
        print("Top topic match counts:")
        for slug, count in sorted(topic_counts.items(), key=lambda x: -x[1])[:20]:
            print(f"  {count:5d}  {slug}")


if __name__ == "__main__":
    main()
