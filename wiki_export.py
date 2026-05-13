#!/usr/bin/env python3
"""
wiki_export.py  —  Export the research wiki to self-contained static HTML.

By default exports: INDEX.md, OPEN-QUESTIONS.md, all topic pages, and all
entity pages. Source pages (wiki/sources/) are excluded by default because
large corpora can contain thousands of them; use --all or --sources to include.

USAGE
    python wiki_export.py [options]

OPTIONS
    -o, --output DIR        Output directory  (default: wiki-export)
        --site-name NAME    Site name shown in browser tab and header
                            (default: "Research Wiki")
        --copyright TEXT    Optional copyright line added to the footer
                            (e.g. "© 2026 Jane Smith")
        --clean             Delete the output directory before exporting
        --all               Include source pages
        --topics            Export only topic pages (+ index docs)
        --entities          Export only entity pages (+ index docs)
        --sources           Export only source pages (+ index docs)
        --no-index          Skip INDEX.md and OPEN-QUESTIONS.md
        --css FILE          Use a custom CSS file instead of the built-in styles
    -q, --quiet             Suppress per-file progress output
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

try:
    import markdown as _md
except ImportError:
    sys.exit(
        "Error: 'markdown' package is required.\n"
        "Install it with:  pip install markdown\n"
        "Or:               pip install -r scripts/requirements.txt"
    )

WIKI_ROOT = Path(__file__).parent
WIKI_DIR = WIKI_ROOT / "wiki"

MD_EXTENSIONS = [
    "tables",
    "fenced_code",
    "toc",
    "sane_lists",
    "smarty",
]

# ---------------------------------------------------------------------------
# Built-in CSS
# ---------------------------------------------------------------------------

BUILTIN_CSS = """\
/* Mnemotron Wiki for Research — static export  */

:root {
    --text:      #1a1a1a;
    --bg:        #fafaf8;
    --accent:    #1d4ed8;
    --border:    #e2e2de;
    --muted:     #6b7280;
    --code-bg:   #f3f4f6;
    --th-bg:     #1e293b;
    --th-fg:     #f1f5f9;
    --stripe:    #f4f4f1;
    --header-bg: #1e293b;
    --header-fg: #cbd5e1;
    --max-w:     860px;
    --radius:    5px;
}

*, *::before, *::after { box-sizing: border-box; }

body {
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 17px;
    line-height: 1.68;
    color: var(--text);
    background: var(--bg);
    margin: 0;
}

/* ── Header / breadcrumb ── */
.site-header {
    background: var(--header-bg);
    color: var(--header-fg);
    padding: 0.55rem 1.5rem;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 0.82rem;
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
}
.site-header a          { color: #93c5fd; text-decoration: none; }
.site-header a:hover    { text-decoration: underline; }
.site-header .sep       { color: #475569; }
.site-header .current   { color: #e2e8f0; font-weight: 600; }

/* ── Main content ── */
main {
    max-width: var(--max-w);
    margin: 2rem auto 5rem;
    padding: 0 1.5rem;
}

/* ── Typography ── */
h1, h2, h3, h4, h5 {
    font-family: system-ui, -apple-system, sans-serif;
    font-weight: 600;
    line-height: 1.3;
    color: #0f172a;
    margin-top: 2rem;
    margin-bottom: 0.5rem;
}
h1 {
    font-size: 1.85rem;
    margin-top: 0.25rem;
    border-bottom: 2px solid var(--border);
    padding-bottom: 0.45rem;
}
h2 { font-size: 1.3rem;  border-bottom: 1px solid var(--border); padding-bottom: 0.2rem; }
h3 { font-size: 1.1rem; }
h4 { font-size: 1rem; }

p  { margin: 0.75rem 0; }
li { margin: 0.25rem 0; }

a               { color: var(--accent); }
a:hover         { text-decoration: underline; }
a:visited       { color: #6d28d9; }

strong          { font-weight: 700; }
hr              { border: none; border-top: 1px solid var(--border); margin: 2rem 0; }

/* ── Tables ── */
table {
    width: 100%;
    border-collapse: collapse;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 0.88rem;
    margin: 1.25rem 0;
    overflow-x: auto;
    display: block;
}
thead th {
    background: var(--th-bg);
    color: var(--th-fg);
    text-align: left;
    padding: 0.5rem 0.8rem;
    font-weight: 600;
    white-space: nowrap;
}
tbody td {
    padding: 0.42rem 0.8rem;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
}
tbody tr:nth-child(even) td { background: var(--stripe); }
tbody tr:hover td           { background: #eef2ff; }

/* ── Code ── */
code {
    font-family: 'SF Mono', 'Fira Code', Consolas, 'Liberation Mono', monospace;
    font-size: 0.84em;
    background: var(--code-bg);
    padding: 0.12em 0.35em;
    border-radius: 3px;
    color: #b91c1c;
}
pre {
    background: #0f172a;
    color: #e2e8f0;
    padding: 1rem 1.25rem;
    border-radius: var(--radius);
    overflow-x: auto;
    font-size: 0.84rem;
    line-height: 1.55;
    margin: 1.25rem 0;
}
pre code {
    background: none;
    padding: 0;
    color: inherit;
    font-size: inherit;
    border-radius: 0;
}

/* ── Blockquotes (used for callout boxes) ── */
blockquote {
    border-left: 4px solid #3b82f6;
    background: #eff6ff;
    margin: 1.25rem 0;
    padding: 0.6rem 1rem 0.6rem 1rem;
    border-radius: 0 var(--radius) var(--radius) 0;
    color: #1e3a5f;
    font-style: normal;
}
blockquote p       { margin: 0.3rem 0; }
blockquote strong  { color: #1e40af; }

/* ── Entity type badge ── */
.entity-badge {
    display: inline-block;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 0.18em 0.55em;
    border-radius: 3px;
    vertical-align: middle;
    margin-left: 0.4rem;
}
.entity-person       { background: #dcfce7; color: #166534; }
.entity-organization { background: #dbeafe; color: #1e40af; }
.entity-place        { background: #fef9c3; color: #713f12; }

/* ── TOC (generated by toc extension) ── */
.toc {
    background: #f8f8f6;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.75rem 1.25rem;
    margin: 1.5rem 0;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 0.875rem;
}
.toc ul   { margin: 0.2rem 0; padding-left: 1.25rem; }
.toc li   { margin: 0.15rem 0; }
.toc a    { color: var(--accent); }

/* ── Footer ── */
.site-footer {
    margin-top: 4rem;
    padding: 1rem 1.5rem;
    border-top: 1px solid var(--border);
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 0.8rem;
    color: var(--muted);
    text-align: center;
}

/* ── Print ── */
@media print {
    .site-header, .site-footer { display: none; }
    body { font-size: 11pt; background: white; }
    main { max-width: none; margin: 0; padding: 0; }
    a    { color: inherit; text-decoration: underline; }
    pre  { background: #f0f0f0; color: black; border: 1px solid #ccc; }
    pre code { color: black; }
}
"""

# ---------------------------------------------------------------------------
# HTML page template
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — {site_name}</title>
  <link rel="stylesheet" href="{css_path}">
</head>
<body>
<header class="site-header">
  <a href="{index_path}">{site_name}</a>
  {breadcrumb}
</header>
<main>
{badge}
{body}
</main>
<footer class="site-footer">
  {footer}
</footer>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Markdown / frontmatter helpers
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_H1_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)


def strip_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text). Parses simple key: value YAML."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm_raw = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    for line in fm_raw.splitlines():
        if ":" in line and not line.startswith(" ") and not line.startswith("-"):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


def get_title(fm: dict, body: str, stem: str) -> str:
    """Derive a display title from frontmatter, first H1, or filename."""
    if "name" in fm:
        return fm["name"]
    if "title" in fm:
        return fm["title"]
    m = _H1_RE.search(body)
    if m:
        return m.group(1).strip()
    return stem.replace("-", " ").title()


def rewrite_links(text: str) -> str:
    """Rewrite .md → .html in relative markdown link URLs."""
    def _sub(m: re.Match) -> str:
        label, href = m.group(1), m.group(2)
        if href.startswith(("http://", "https://", "mailto:", "#", "/")):
            return m.group(0)
        new_href = re.sub(r"\.md(#[^)]*)?$",
                          lambda mm: ".html" + (mm.group(1) or ""), href)
        return f"[{label}]({new_href})"
    return _LINK_RE.sub(_sub, text)


def md_to_html(text: str) -> str:
    """Convert markdown body text to HTML."""
    return _md.markdown(
        text,
        extensions=MD_EXTENSIONS,
        extension_configs={"toc": {"title": "Contents"}},
    )


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

def _section_label(section: str) -> str:
    return {"topics": "Topics", "entities": "Entities", "sources": "Sources"}.get(section, "")


def _build_footer(site_name: str, copyright_text: str) -> str:
    parts = []
    if copyright_text:
        parts.append(copyright_text)
    parts.append(
        f'Built with <a href="https://github.com/pwallace/mnemotron-wiki-r">Mnemotron-R</a>'
    )
    return " &middot; ".join(parts)


def render_page(
    title: str,
    body_html: str,
    section: str,
    depth: int,
    site_name: str = "Research Wiki",
    copyright_text: str = "",
    entity_type: str = "",
    css_override: str = "",
) -> str:
    """Wrap converted HTML in the full page template."""
    prefix = "../" * depth
    css_path = css_override if css_override else f"{prefix}wiki.css"
    index_path = f"{prefix}index.html"

    # Breadcrumb
    if section:
        label = _section_label(section)
        section_href = f"{prefix}{section}/index.html"
        breadcrumb = (
            f'<span class="sep">/</span> '
            f'<a href="{section_href}">{label}</a> '
            f'<span class="sep">/</span> '
            f'<span class="current">{title}</span>'
        )
    else:
        breadcrumb = f'<span class="sep">/</span> <span class="current">{title}</span>'

    # Entity type badge
    badge = ""
    if entity_type:
        css_cls = f"entity-{entity_type}"
        badge = f'<span class="entity-badge {css_cls}">{entity_type}</span>\n'

    footer = _build_footer(site_name, copyright_text)

    return PAGE_TEMPLATE.format(
        title=title,
        site_name=site_name,
        css_path=css_path,
        index_path=index_path,
        breadcrumb=breadcrumb,
        badge=badge,
        body=body_html,
        footer=footer,
    )


def make_section_index(
    section: str,
    pages: list[tuple[str, Path]],
    depth: int,
    site_name: str = "Research Wiki",
    copyright_text: str = "",
) -> str:
    """Generate a simple index page for a section (Topics, Entities, Sources)."""
    label = _section_label(section)
    prefix = "../" * depth
    rows = "\n".join(
        f'<li><a href="{p.stem}.html">{title}</a></li>'
        for title, p in sorted(pages, key=lambda x: x[0].lower())
    )
    body_html = f"<h1>{label}</h1>\n<ul>\n{rows}\n</ul>\n"
    return render_page(
        label, body_html, "", depth,
        site_name=site_name,
        copyright_text=copyright_text,
        css_override=f"{prefix}wiki.css",
    )


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_files(args: argparse.Namespace) -> list[Path]:
    """Return a list of source .md paths to export based on CLI flags."""
    files: list[Path] = []

    include_topics = args.topics or (not args.topics and not args.entities and not args.sources)
    include_entities = args.entities or (not args.topics and not args.entities and not args.sources)
    include_sources = args.sources or args.all

    if include_topics:
        files += sorted((WIKI_DIR / "topics").glob("*.md"))
    if include_entities:
        files += sorted((WIKI_DIR / "entities").glob("*.md"))
    if include_sources:
        files += sorted((WIKI_DIR / "sources").glob("*.md"))

    if not args.no_index:
        for name in ("INDEX.md", "OPEN-QUESTIONS.md"):
            p = WIKI_DIR / name
            if p.exists():
                files.append(p)

    return files


# ---------------------------------------------------------------------------
# Export a single file
# ---------------------------------------------------------------------------

def export_file(
    src: Path,
    out_dir: Path,
    site_name: str = "Research Wiki",
    copyright_text: str = "",
    quiet: bool = False,
) -> Path:
    """Convert src .md to HTML and write to out_dir. Returns the output path."""
    text = src.read_text(encoding="utf-8", errors="replace")
    fm, body = strip_frontmatter(text)
    title = get_title(fm, body, src.stem)
    entity_type = fm.get("entity_type", "")

    # Determine section and output path
    try:
        rel = src.relative_to(WIKI_DIR)
    except ValueError:
        rel = src.relative_to(WIKI_ROOT)

    parts = rel.parts
    if len(parts) == 1:
        section = ""
        depth = 0
        out_path = out_dir / rel.with_suffix(".html").name.lower().replace("_", "-")
        if src.name == "INDEX.md":
            out_path = out_dir / "index.html"
    else:
        section = parts[0]
        depth = 1
        out_path = out_dir / section / (src.stem + ".html")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    body_with_new_links = rewrite_links(body)
    body_html = md_to_html(body_with_new_links)
    page_html = render_page(
        title, body_html, section, depth,
        site_name=site_name,
        copyright_text=copyright_text,
        entity_type=entity_type,
    )

    out_path.write_text(page_html, encoding="utf-8")
    if not quiet:
        print(f"  {out_path.relative_to(out_dir)}")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the research wiki to static HTML.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-o", "--output", default="wiki-export",
                        help="Output directory (default: wiki-export)")
    parser.add_argument("--site-name", default="Research Wiki",
                        help='Site name in browser tab and header (default: "Research Wiki")')
    parser.add_argument("--copyright", default="", metavar="TEXT",
                        help='Optional copyright line added to the footer, e.g. "© 2026 Jane Smith"')
    parser.add_argument("--clean", action="store_true",
                        help="Delete output directory before exporting")
    parser.add_argument("--all", action="store_true",
                        help="Include source pages (can be large for big corpora)")
    parser.add_argument("--topics", action="store_true",
                        help="Include only topic pages (+ index docs)")
    parser.add_argument("--entities", action="store_true",
                        help="Include only entity pages (+ index docs)")
    parser.add_argument("--sources", action="store_true",
                        help="Include only source pages (+ index docs)")
    parser.add_argument("--no-index", action="store_true",
                        help="Skip INDEX.md and OPEN-QUESTIONS.md")
    parser.add_argument("--css", metavar="FILE",
                        help="Use a custom CSS file instead of built-in styles")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress per-file progress output")
    args = parser.parse_args()

    site_name = args.site_name
    copyright_text = args.copyright
    out_dir = Path(args.output).resolve()

    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
        if not args.quiet:
            print(f"Cleaned: {out_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Write CSS
    if args.css:
        css_src = Path(args.css)
        if not css_src.exists():
            sys.exit(f"Error: CSS file not found: {args.css}")
        shutil.copy(css_src, out_dir / "wiki.css")
        if not args.quiet:
            print(f"CSS: {css_src} → wiki.css")
    else:
        (out_dir / "wiki.css").write_text(BUILTIN_CSS, encoding="utf-8")

    # Collect files
    files = collect_files(args)
    if not files:
        sys.exit("No files to export. Check your --topics/--entities/--sources flags.")

    if not args.quiet:
        print(f"Exporting {len(files)} pages to {out_dir}/")

    # Export each file; track pages per section for section indexes
    section_pages: dict[str, list[tuple[str, Path]]] = {
        "topics": [], "entities": [], "sources": []
    }

    for src in files:
        try:
            rel = src.relative_to(WIKI_DIR)
        except ValueError:
            rel = src.relative_to(WIKI_ROOT)

        parts = rel.parts
        fm, body = strip_frontmatter(src.read_text(encoding="utf-8", errors="replace"))
        title = get_title(fm, body, src.stem)

        out_path = export_file(
            src, out_dir,
            site_name=site_name,
            copyright_text=copyright_text,
            quiet=args.quiet,
        )

        if len(parts) > 1 and parts[0] in section_pages:
            section_pages[parts[0]].append((title, out_path))

    # Write section index pages
    for section, pages in section_pages.items():
        if not pages:
            continue
        idx_path = out_dir / section / "index.html"
        idx_html = make_section_index(
            section, [(t, p) for t, p in pages], depth=1,
            site_name=site_name,
            copyright_text=copyright_text,
        )
        idx_path.write_text(idx_html, encoding="utf-8")
        if not args.quiet:
            print(f"  {section}/index.html  ({len(pages)} pages)")

    # Ensure a root index.html exists even if INDEX.md wasn't exported
    root_index = out_dir / "index.html"
    if not root_index.exists():
        fallback = [f"<h1>{site_name}</h1>\n<ul>"]
        for section in ("topics", "entities", "sources"):
            idx = out_dir / section / "index.html"
            if idx.exists():
                fallback.append(
                    f'<li><a href="{section}/index.html">{_section_label(section)}</a></li>'
                )
        fallback.append("</ul>")
        body_html = "\n".join(fallback)
        root_index.write_text(
            render_page(
                site_name, body_html, "", 0,
                site_name=site_name,
                copyright_text=copyright_text,
            ),
            encoding="utf-8",
        )

    total = sum(1 for _ in out_dir.rglob("*.html"))
    if not args.quiet:
        print(f"\nDone. {total} HTML files written to {out_dir}/")
        print(f"Open:  {out_dir}/index.html")


if __name__ == "__main__":
    main()
