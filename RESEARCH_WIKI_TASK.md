# Mnemotron Wiki for Research — Ingest Task

Instructions for Claude when running a research wiki ingest. Can be triggered
on demand ("run the research wiki ingest task") or on a schedule.

---

## Overview

The ingest task runs in four stages:

0. **Corpus assessment & taxonomy** — before processing new files, assess what
   they are and establish or extend the topic and entity taxonomy.
1. **Document ingest** — process all new files in `ingest/` into `wiki/sources/`
   and synthesize or update `wiki/topics/` pages.
2. **Index update** — regenerate `wiki/INDEX.md`.
3. **Git commit** — commit all changes with a dated message.

---

## Paths

All paths are relative to the wiki root (the directory containing this file).

| Purpose | Path |
|---------|------|
| Drop zone for new documents | `ingest/` |
| Failed ingest quarantine | `ingest/failed/` |
| Retained source transcriptions | `wiki/sources/` |
| Synthesized topic pages | `wiki/topics/` |
| Entity pages (people, orgs, places) | `wiki/entities/` |
| Main index | `wiki/INDEX.md` |
| Content manifest | `.manifest.json` |

---

## Stage 0: Corpus Assessment and Taxonomy

Run this stage **before processing any new files**. It ensures topics and
entities exist to receive links from source pages, and prevents source pages
from being islands with no taxonomy connections.

### 0.1 Understand the corpus

Read the list of new files from `check_ingest.py`. Before touching any file,
answer these questions:

- **What kind of material is this?**  
  (newspaper issues, archival documents, academic papers, field notes, etc.)
- **What institution, project, or subject is the primary focus?**
- **What time period does it cover?**
- **Is this a homogeneous batch** (many files of the same type, e.g., newspaper
  issues) **or a mixed batch** (varied document types)?

For batches of more than 5 files, write a brief assessment (2–4 sentences) in
your working context before continuing. For large batches (50+ files), consider
a quick sample: read 3–5 files at random and note the recurring subjects,
names, and themes you observe.

### 0.2 Establish or extend the taxonomy

**Minimum taxonomy deliverables before any source pages are written:**

#### Topics

Create `wiki/topics/` pages for each major thematic category present in the
corpus. Use the table below as a starting checklist — not all categories apply
to every corpus, but most research projects touch several of them:

| Category | When to create |
|----------|---------------|
| **Overview / collection** | Always, for any batch > 5 files from the same source |
| **People and biography** | When the corpus focuses on specific individuals |
| **Institutional history** | When the primary subject is an organization or institution |
| **Athletics / sports** | When sports coverage is substantial |
| **Governance & administration** | When policy, leadership, or decision-making recurs |
| **Campus / physical environment** | When built spaces and facilities are discussed |
| **Social life & culture** | When daily life, events, or community practices are documented |
| **Politics & activism** | When political or social movements appear |
| **Arts, culture & publications** | When cultural production is documented |
| **Science & research** | When scholarly or scientific work is the subject |
| **Economics & finance** | When funding, costs, or economic conditions are central |

Topic pages do not require having read every source. Write them from what you
know about the corpus type and what the sample files reveal. Leave `## Open
Questions` generously populated — they will guide future reading.

#### Entities

Create `wiki/entities/` pages for entities that are **certain to recur** across
the corpus. At minimum:

- **The primary institution(s)** (college, organization, project) — always
- **Major sub-organizations** (departments, teams, publications, committees)
  when they are the recurring subject of coverage
- **Physical locations** that serve as central anchors (buildings, campuses,
  sites) when they appear repeatedly
- **Named people** only if they are central figures with substantial documented
  roles, not merely mentioned in passing; prefer to accumulate mentions in source
  pages first, then create an entity page once the relevance is clear

Entity pages for named individuals are expensive to maintain accurately.
Default to creating organization and place entities; save person entities for
figures who appear in many sources or who are central to the research questions.

### 0.3 Check existing taxonomy

Before creating any new topic or entity page, check whether one already exists:

```bash
ls wiki/topics/
ls wiki/entities/
```

If an existing page covers the subject, plan to **update** it rather than
create a duplicate. If a new batch significantly extends an existing topic
(e.g., 50 more newspaper issues added to an archive with an existing overview
page), update the overview page's scope statement and open questions.

### 0.4 Configure project permissions (required before running background agents)

Background synthesis agents run without inheriting the parent session's tool
permissions. Before running any parallel agents, verify that
`.claude/settings.json` exists at the project root:

```bash
ls .claude/settings.json 2>/dev/null || echo "MISSING — create it"
```

If missing, create it:

```json
{
  "permissions": {
    "allow": [
      "Edit",
      "Write",
      "Bash(grep*)",
      "Bash(find*)",
      "Bash(git add*)",
      "Bash(git commit*)",
      "Bash(git status*)",
      "Bash(git log*)",
      "Bash(wc*)",
      "Bash(ls*)"
    ]
  }
}
```

Without this file, agents will be denied `Edit` and `Write` permissions and
will either fail silently or fall back to full-file `Write` operations that
risk overwriting concurrent changes by other agents.

---

## Stage 1: Document Ingest

### 1.1 Find new files

```bash
python scripts/check_ingest.py
```

Prints one filepath per line for each file in `ingest/` not yet in the manifest.
If output is empty, skip to Stage 2.

### 1.2 Classify each file

For each file, determine its **content type**:

| Type | Criteria | Pipeline |
|------|----------|----------|
| **Native text** | `.txt`, `.md`, `.html`, `.htm`, `.csv`, `.docx`, `.odt` | Extract → source page |
| **Native PDF** | `.pdf` where text extraction succeeds | Extract → source page |
| **Scanned PDF** | `.pdf` where extraction yields < 100 chars | OCR → source page |
| **Print scan** | `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff` — typed or printed content | OCR (Tesseract → Claude) → source page |
| **Handwritten scan** | `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff` — handwritten content | OCR (Claude Vision) → source page |

When the content type of an image file is ambiguous, default to **print**.
Filenames ending in `-hw`, `-handwritten`, or `-written` indicate handwritten.

### 1.3a Text extraction (native text and native PDFs)

```bash
python scripts/extract_text.py <filepath>
```

- **Exit 0**: text printed to stdout — proceed to 1.4.
- **Exit 1**: extraction error — move file to `ingest/failed/`, log the error, continue.
- **Exit 2**: PDF is a scan (no text layer) — re-route to 1.3b.

### 1.3b OCR pipeline (scanned images and scanned PDFs)

```bash
python scripts/ocr.py <filepath>
# or, for known handwritten content:
python scripts/ocr.py <filepath> --hint handwritten
```

The script:
1. Converts TIFF/PNG to JPEG (Pillow).
2. For PDFs: rasterizes each page at 300 DPI.
3. Runs Tesseract (offline) for print content; evaluates quality.
4. Falls back to Claude Vision (claude-sonnet-4-6) if Tesseract quality is too
   low, or if hint is "handwritten".

Progress is printed to stderr; extracted text goes to stdout.

- **Exit 0**: text printed to stdout — proceed to 1.4.
- **Exit 1**: OCR failed — move file to `ingest/failed/`, log the error, continue.

### 1.4 Write the source page

Save the extracted/transcribed content to `wiki/sources/<slug>.md`.

Generate `<slug>` from the original filename: lowercase, spaces and underscores
to hyphens, strip extension.  If a page with that slug already exists, append
`-2`, `-3`, etc.

**Source page template:**

```markdown
---
title: "[Inferred document title, or filename if unclear]"
type: pdf | scan-print | scan-handwritten | web | notes | data
ocr_method: tesseract | claude | tesseract+claude | pdfminer | direct
ingested: YYYY-MM-DD
original_file: original-filename.ext
tags:
  - [tag]
---

# [Document Title]

## Source Information

[1–3 sentences: what this document is, its likely origin or author, approximate
date if determinable from content, and any relevant context about provenance.]

## Content

[Full extracted or transcribed text, lightly formatted as markdown where
structure is evident — use headings for sections, bullet points for lists,
and preserve paragraph breaks. Do not editorialize; transcribe what is there.]

## Notes

[Optional: flag any OCR uncertainty, illegible sections, or notable quality
issues. Omit this section if the extraction is clean.]
```

Do **not** delete the source file yet — wait until the manifest step succeeds.

### 1.4b Review and repair OCR output

**Apply this step to every source whose `ocr_method` is `tesseract` or
`tesseract+claude`.** Native text extraction (pdfminer, direct read) does not
require it.

Before writing the source page, read the raw OCR text carefully and apply the
repairs below. The goal is to produce a clean, faithful transcription — not to
interpret or editorialize the content.

#### What to fix

**Character substitutions** — correct only when context makes the intended
reading unambiguous:

| Common confusion | Typical context clue |
|-----------------|----------------------|
| `0` ↔ `O` | surrounded by letters vs. digits |
| `1` ↔ `l` ↔ `I` | grammatical role (article "I", digit, letter in word) |
| `rn` → `m` | "rnoderate" → "moderate" |
| `cl` → `d` | "cloes" → "does" |
| `vv` → `w` | "vvater" → "water" |
| `ﬁ` → `fi`, `ﬂ` → `fl`, `ﬀ` → `ff` | ligature encoding artifacts |

**Hyphenated line breaks** — join when the hyphen is a formatting artifact, not
a semantic hyphen:
- "doc-\nument" → "document"
- Leave "well-known", "co-author", etc. untouched.

**Noise lines** — remove lines that are clearly OCR garbage:
- Lines consisting only of punctuation with no alphabetic characters
- Lines of 1–2 characters that are isolated (not part of a list or table)
- Repeated identical short lines (running header/footer bleed)

**Spacing** — normalize silently:
- Collapse runs of multiple spaces to one
- Consolidate more than two consecutive blank lines to two

**Ligatures and encoding** — fix silently:
- `ﬁ` → fi, `ﬂ` → fl, `ﬀ` → ff, `ﬃ` → ffi, `ﬄ` → ffl

#### What to mark, not fix

- **Uncertain reading** — use `[?word?]` when a word is plausible but not certain.
- **Illegible passage** — use `[illegible]` for sequences you cannot
  confidently read.
- **Tables** — Tesseract rarely reproduces table structure reliably. Replace
  garbled table content with `[Table — OCR unreliable; verify against original]`
  and describe the apparent column headers in a sentence if they are readable.
- **Mathematical or chemical formulas** — replace with
  `[Formula — OCR unreliable; verify against original]`.
- **Column layout confusion** — if text from adjacent columns appears
  interleaved, note `[Note: two-column layout detected; column order may be
  incorrect in the following passage]` at the affected section.

#### What NOT to do

- Do not correct spelling errors in the underlying text itself.
- Do not rephrase, paraphrase, or improve awkward phrasing.
- Do not guess at heavily corrupted multi-word passages — use `[illegible]`.
- Do not restructure or reorder content even if the layout seems confused.

#### Flag for manual review

Add a `## Notes` section to the source page and include a manual-review flag
if any of the following apply:

- More than roughly 10% of lines appear to be noise after cleanup.
- A two-column or complex layout is present and interleaving is apparent.
- Mathematical notation, chemical structures, or specialized symbols appear
  in quantity.
- The OCR method was `tesseract+claude` with a high Claude page ratio — this
  suggests the scan quality is poor and the transcription may have significant
  gaps.

```
## Notes

OCR quality: [good / fair / poor — brief explanation]
Manual review recommended: [yes / no — reason if yes]
```

### 1.5 Synthesize or update topic pages

> **Two synthesis modes exist.** This step (Stage 1.5) is the *breadth-first*
> mode: read a source, update the topics it touches. This is essential for
> discovering new themes and ensuring the wiki's coverage is wide. Stage 4
> is the *depth-first* mode: start from an open question and grep for the
> sources that answer it. Both are necessary. Stage 1.5 identifies the
> questions; Stage 4 answers them. Alternate between modes rather than
> committing exclusively to one.

After writing the source page, analyze its content:

- **Does an existing `wiki/topics/` page cover this subject?**
  - Yes → update it: add new key points from this source, link to the new
    source page in the Sources table.
  - No → create a new topic page at `wiki/topics/<slug>.md`.
- **Are there named people, organizations, or places central to this source?**
  - Check `wiki/entities/` for an existing page.
  - If yes: update with new information.
  - If no and the entity is substantively relevant (not just mentioned in
    passing): create a new entity page.

**Depth guidance by batch size:**

| Batch size | Topic synthesis approach |
|------------|-------------------------|
| 1–5 files | Full per-file synthesis: read each source page, update or create topic and entity pages for every major subject. |
| 6–50 files | Pre-establish taxonomy in Stage 0; link each source page to 1–3 existing topic pages; update topic pages with specific key points from the most significant sources in the batch (not necessarily every file). |
| 50+ files | Pre-establish full taxonomy in Stage 0 (including overview topic and all major thematic topics); batch-process source page creation; then do a dedicated synthesis pass on a representative sample (e.g., earliest, latest, and mid-range issues plus any that appear thematically rich). Update topic pages with findings from the sample, and note that the full corpus warrants deeper synthesis. |

**For large batches, the minimum synthesis deliverables are:**
- An overview/collection topic page describing the full corpus
- At least 5 thematic topic pages with Key Points grounded in specific sources
- Entity pages for the primary institution(s) and any organization that recurs substantially across the corpus
- All source pages linked to at least one topic page in the Related Topics section

**Topic page template:**

```markdown
---
updated: YYYY-MM-DD
tags:
  - [tag]
---

# [Topic Title]

## Overview

[2–4 sentences describing the topic, its scope, and its relevance to the
research.]

## Key Points

[Prose paragraphs synthesizing what is known. Cite sources inline using
relative links: ([Source Title](../sources/slug.md)).]

## Open Questions

[Bulleted list of unresolved questions or gaps this topic raises. Update
as sources accumulate.]

## Sources

| Source | Date Ingested | Contribution |
|--------|--------------|--------------|
| [Title](../sources/slug.md) | YYYY-MM-DD | [What this source adds to the topic] |

## Related Topics

- [Links to other topic pages]
```

**Entity page template:**

File: `wiki/entities/<slug>.md`

```markdown
---
name: "[Full Name or Official Name]"
entity_type: person | organization | place
updated: YYYY-MM-DD
---

# [Name]

## Overview

[1–2 sentences: who or what this is, and why it is relevant to the research.]

## Relevance to Research

[2–4 sentences describing how this entity figures in the source material.]

## Notes

[Any useful details: dates, affiliations, locations, relationships.]

## Related Sources

- [Links to wiki/sources/ pages where this entity appears]

## Related Topics

- [Links to wiki/topics/ pages]
```

### 1.6 Mark in manifest and delete the original

Mark the file as processed:

```python
python - <<'EOF'
import sys; sys.path.insert(0, ".")
from pathlib import Path
from scripts.manifest import load_manifest, mark_processed, save_manifest

filepath = Path("<original filepath>")
wiki_page = Path("wiki/sources/<slug>.md")
manifest = load_manifest()
manifest = mark_processed(filepath, wiki_page, manifest)
save_manifest(manifest)
print(f"Marked: {filepath.name}")
EOF
```

Only after the manifest step succeeds, delete the original:

```bash
rm <filepath>
```

**Retention rule:** The markdown source page in `wiki/sources/` is the
canonical retained form. Raw scans, PDFs, and image files are discarded after
ingest. If the original is a native markdown or text file, it may be deleted
since its content is preserved verbatim in the source page.

---

## Stage 2: Index Update

Rewrite `wiki/INDEX.md`:

1. **Sources** — list all files in `wiki/sources/`, alphabetically, with title
   and ingested date from frontmatter.
2. **Topics** — list all files in `wiki/topics/`, alphabetically, with 1-sentence
   summary from the Overview section.
3. **Entities** — list all files in `wiki/entities/`, alphabetically, with
   entity_type from frontmatter.
4. **Ingested Documents** — read `.manifest.json` and list filename, date
   processed, and wiki/sources/ page.

**INDEX.md template:**

```markdown
---
updated: YYYY-MM-DD
---

# Research Wiki Index

## Sources

| Title | Type | Ingested |
|-------|------|---------|
| [Title](sources/slug.md) | pdf / scan-print / etc. | YYYY-MM-DD |

## Topics

| Topic | Summary |
|-------|---------|
| [Topic Title](topics/slug.md) | [1-sentence summary] |

## Entities

| Name | Type |
|------|------|
| [Name](entities/slug.md) | person / organization / place |

## Ingested Documents Log

| Original File | Date Processed | Source Page |
|---------------|---------------|-------------|
| filename.ext | YYYY-MM-DD | [slug](sources/slug.md) |
```

---

## Stage 3: Git Commit

```bash
git add wiki/ .manifest.json
git commit -m "wiki update $(date +%Y-%m-%d)"
```

If nothing has changed since the last run, skip the commit.

---

## Stage 4: Deep Synthesis Pass (question-driven, targeted)

Run this stage **after** Stage 1 has established broad coverage of the corpus.
It answers specific open questions by grepping for relevant source files rather
than reading files in sequence. It complements Stage 1 rather than replacing
it — Stage 1 ensures no major topics are missed; Stage 4 answers the questions
Stage 1 surfaces.

**When to run Stage 4 instead of another Stage 1 pass:**
- The wiki has at least one full breadth pass behind it (all or most source
  files linked to at least one topic page, Open Questions sections populated)
- You want to go deeper on a specific question rather than wider across the
  corpus
- Two or more topic pages share a gap that a single targeted search can fill

**Warning: Stage 4 is depth-optimized and can create blind spots.** Running
Stage 4 repeatedly without returning to Stage 1 risks a wiki that goes 10
levels deep on a few topics while missing others entirely. Alternate between
modes: one Stage 1 breadth pass for every 2–3 Stage 4 depth passes is a
reasonable rhythm for large corpora.

### 4.1 Identify open questions

Check for a compiled open questions document:

```bash
cat wiki/OPEN-QUESTIONS.md
```

If this file does not exist, scan the `## Open Questions` sections across all
topic pages:

```bash
grep -h -A 30 "^## Open Questions" wiki/topics/*.md | grep "^- " | sort -u
```

Select 2–4 questions that are:
- **Specific enough to grep for** — answerable by searching for known terms,
  names, or dates in the source corpus
- **Cross-cutting** — findings that update multiple topic pages are highest
  value per unit of work
- **Independent** — if two questions touch different topic pages and different
  source ranges, they can be run as parallel background agents

### 4.2 Research by question, not by file

For each question:

1. Grep the source corpus for terms that would appear in relevant files:
   ```bash
   grep -rn "term1\|term2\|term3" wiki/sources/ 2>/dev/null | head -20
   ```
2. Identify the 3–6 most relevant files from the results
3. For each file: grep for specific line numbers, then `Read` ±80 lines around
   each hit — do not read files from line 1 unless the whole file is relevant
4. Follow the question across whichever files answer it; stop when the answer
   is clear rather than reading everything that mentions the topic

### 4.3 Write additively using Edit

After research, use `Edit` to insert new Key Points and source table rows into
existing topic pages. See the **Additive editing rule** in Style notes below.

### 4.4 Parallel agents for independent questions

When two or more open questions are independent, launch them as parallel
background agents. Each agent should:
- Have one clearly bounded question or subject area in its prompt
- Target specific named source files or ranges identified by prior grep
- Write to non-overlapping topic pages where possible
- Commit independently with a descriptive message

Provide each agent with the exact grep commands needed to find relevant
sources, the names of the topic files it should write to, and explicit
instructions to use `Edit` (not `Write`) and to read the current file state
before editing.

### 4.5 Maintain OPEN-QUESTIONS.md

After Stage 4, update or create `wiki/OPEN-QUESTIONS.md`:
- Remove questions that are now answered
- Update partially-answered questions to reflect current knowledge
- Mark high-priority remaining questions with `**[Priority]**`

If the file does not yet exist, create it by extracting all Open Questions
bullet points from topic pages, grouped by topic with links:

```bash
grep -h -A 30 "^## Open Questions" wiki/topics/*.md | grep "^- " | sort -u
```

---

## Style notes

- Write all wiki pages in plain prose. Use bullet lists only where list
  structure genuinely aids comprehension.
- Use relative links between pages (`[Title](../topics/slug.md)`) so the wiki
  stays portable across machines.
- Frontmatter is YAML; quote all string values.

### Additive editing rule (strictly enforced)

Topic and entity pages grow over time and may be modified by concurrent agents.
**Always use `Edit` (not `Write`) when updating an existing topic or entity
page.** `Write` replaces the entire file and will silently destroy content
added by other agents or sessions running in parallel.

The only valid uses of `Write` on wiki pages are:
- Creating a brand new page that does not yet exist
- Complete rewrite of a stub page that has no substantive content yet

Before any `Edit`, confirm you have read the current file state with `Read`.
If you receive a "file modified since read" error, re-read the current file
before retrying — never proceed with stale content.

### Source pages (transcription rules)

- Source pages are **faithful transcriptions**, not interpretations. Repair
  mechanical OCR artifacts (see 1.4b); do not editorialize.
- Uncertain readings: `[?word?]`. Illegible passages: `[illegible]`.
- Tables and formulas that OCR cannot reproduce: flag with a bracketed note
  rather than attempting to reconstruct them.
- If the OCR method was Tesseract on any page, always apply the 1.4b repair
  pass before writing the final source page — even if the output looks
  reasonable at a glance. Tesseract noise is often subtle.

### Topic pages (synthesis rules)

- Topic pages interpret and synthesize; source pages transcribe. Keep the
  distinction clear.
- Cite specific source pages for claims: `([Title](../sources/slug.md))`.
- Note when a finding comes from a low-quality scan or uncertain OCR passage.

---

---

*To the extent possible under law, Patrick R. Wallace, Hamilton College LITS
has waived all copyright and related or neighboring rights to this document.
This work is dedicated to the public domain under CC0 1.0 Universal.
Full text: <https://creativecommons.org/publicdomain/zero/1.0/>*

*SPDX-License-Identifier: CC0-1.0*
