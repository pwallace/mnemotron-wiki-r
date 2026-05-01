#!/usr/bin/env bash
# setup.sh — One-time initialization for Mnemotron Wiki for Research.
# Run this once from the wiki root after cloning or creating the project.

set -euo pipefail
WIKI_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$WIKI_ROOT"

echo "=== Mnemotron Wiki for Research — Setup ==="
echo "Wiki root: $WIKI_ROOT"
echo

# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------

echo "Creating directories..."
mkdir -p ingest/failed
mkdir -p wiki/sources wiki/topics wiki/entities
echo "  OK"

# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

if [ ! -d ".git" ]; then
  echo "Initializing git repository..."
  git init
  echo "  OK"
else
  echo "Git repository already exists — skipping init."
fi

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------

echo
echo "Installing Python dependencies..."
pip install -r scripts/requirements.txt
echo "  OK"

# ---------------------------------------------------------------------------
# System dependency checks (warn only — do not fail)
# ---------------------------------------------------------------------------

echo
echo "Checking system dependencies..."

# Tesseract OCR
if command -v tesseract &>/dev/null; then
  echo "  tesseract: $(tesseract --version 2>&1 | head -1)  ✓"
else
  echo "  tesseract: NOT FOUND"
  echo "    → Install with:  brew install tesseract"
  echo "    → Without Tesseract, all OCR will fall back to Claude Vision (API costs apply)."
fi

# poppler (required by pdf2image)
if command -v pdftoppm &>/dev/null; then
  echo "  poppler (pdftoppm): found  ✓"
else
  echo "  poppler: NOT FOUND"
  echo "    → Install with:  brew install poppler"
  echo "    → Without poppler, scanned PDF processing will not work."
fi

# ---------------------------------------------------------------------------
# Initialize manifest
# ---------------------------------------------------------------------------

if [ ! -f ".manifest.json" ]; then
  echo "{}" > .manifest.json
  echo
  echo "Created empty .manifest.json"
fi

# ---------------------------------------------------------------------------
# Starter index
# ---------------------------------------------------------------------------

if [ ! -f "wiki/INDEX.md" ]; then
  DATE=$(date +%Y-%m-%d)
  cat > wiki/INDEX.md <<EOF
---
updated: $DATE
---

# Research Wiki Index

*Index will be populated automatically by the ingest task.*
EOF
  echo "Created wiki/INDEX.md"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo
echo "=== Setup complete ==="
echo
echo "Next steps:"
echo "  1. Drop documents into ingest/"
echo "  2. Run the ingest task:  tell Claude 'run the research wiki ingest task'"
echo "     (Claude reads RESEARCH_WIKI_TASK.md for instructions)"
echo "  3. For scanned PDFs or image files, ensure Tesseract and poppler are"
echo "     installed, and set ANTHROPIC_API_KEY for the Claude Vision fallback."
echo
