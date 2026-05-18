#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Mnemotron Wiki — scripts/open_questions.py
# Originally developed by Patrick R. Wallace, Hamilton College LITS.
# Licensed under the GNU General Public License v3 or later.
# See <https://www.gnu.org/licenses/gpl-3.0.html>.

"""
open_questions.py — Regenerate wiki/OPEN-QUESTIONS.md from all topic pages.

Scans the ## Open Questions section of every wiki/topics/*.md file, groups
questions by topic (with links), deduplicates exact matches, and writes a
consolidated OPEN-QUESTIONS.md.

Run automatically as part of Stage 2 (index update) after any ingest or
synthesis pass so the research agenda stays current.

Usage:
    python scripts/open_questions.py
    python scripts/open_questions.py --dry-run     # print output, do not write
    python scripts/open_questions.py --count       # print question counts only
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WIKI_ROOT  = Path(__file__).resolve().parent.parent
TOPICS_DIR = WIKI_ROOT / "wiki" / "topics"
OQ_PATH    = WIKI_ROOT / "wiki" / "OPEN-QUESTIONS.md"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _read_open_questions(path: Path) -> list:
    """Return a list of question strings from the ## Open Questions section."""
    content = path.read_text(encoding="utf-8")
    m = re.search(r"## Open Questions\n\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if not m:
        return []
    block = m.group(1).strip()
    questions = []
    for line in block.splitlines():
        stripped = line.strip()
        # Bullet items (- or *) only; skip sub-items and blank lines
        if re.match(r"^[-*]\s+", stripped):
            q = re.sub(r"^[-*]\s+", "", stripped).strip()
            if q:
                questions.append(q)
    return questions


def _topic_title(path: Path) -> str:
    """Return the H1 title from a topic page, or a slug-derived fallback."""
    content = path.read_text(encoding="utf-8")
    h1 = re.search(r"^# (.+)$", content, re.MULTILINE)
    return h1.group(1).strip() if h1 else path.stem.replace("-", " ").title()


def collect(topics_dir: Path) -> list:
    """Return [(slug, title, [questions])] for all topics with open questions.

    Sorted alphabetically by slug; questions deduplicated within each topic.
    """
    results = []
    for topic_path in sorted(topics_dir.glob("*.md")):
        questions_raw = _read_open_questions(topic_path)
        if not questions_raw:
            continue
        # Deduplicate while preserving order
        seen = set()
        questions = []
        for q in questions_raw:
            key = re.sub(r"\s+", " ", q).lower().strip("?. ")
            if key not in seen:
                seen.add(key)
                questions.append(q)
        results.append((topic_path.stem, _topic_title(topic_path), questions))
    return results


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(data: list) -> str:
    """Render the consolidated OPEN-QUESTIONS.md content."""
    total = sum(len(qs) for _, _, qs in data)
    today = date.today().isoformat()

    lines = [
        "---",
        f'updated: "{today}"',
        "---",
        "",
        "# Open Research Questions",
        "",
        f"*{total} open questions across {len(data)} topic pages.*  ",
        "*Edit questions in individual topic pages; this file is auto-generated*",
        "*by `scripts/open_questions.py` and regenerated as part of Stage 2.*",
        "",
        "---",
        "",
    ]

    for slug, title, questions in data:
        lines.append(f"## [{title}](topics/{slug}.md)")
        lines.append("")
        for q in questions:
            lines.append(f"- {q}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rendered output to stdout; do not write file")
    parser.add_argument("--count", action="store_true",
                        help="Print per-topic question counts and exit")
    args = parser.parse_args()

    data = collect(TOPICS_DIR)

    if args.count:
        total = 0
        for slug, title, questions in data:
            print(f"  {len(questions):3d}  {title}")
            total += len(questions)
        print(f"\n  {total:3d}  TOTAL across {len(data)} topics")
        return

    content = render(data)

    if args.dry_run:
        print(content)
        return

    OQ_PATH.write_text(content, encoding="utf-8")
    total = sum(len(qs) for _, _, qs in data)
    print(f"Wrote {OQ_PATH.relative_to(WIKI_ROOT)} "
          f"({total} questions, {len(data)} topics)")


if __name__ == "__main__":
    main()
