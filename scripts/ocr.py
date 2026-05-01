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
ocr.py — Offline-first OCR pipeline for scanned images and scanned PDFs.

Design
------
The pipeline is structured to minimise Claude Vision API calls while still
producing high-quality transcriptions:

  1. Convert input to per-page JPEG files.
       - TIFF/PNG: Pillow converts each frame to JPEG.
       - PDF: pdf2image rasterises each page at PDF_OCR_DPI (default 300 DPI).

  2. Handwritten hint → skip directly to Claude Vision for all pages.
     There is no offline handwriting OCR engine included; Tesseract's
     handwriting support is unreliable without custom training data.

  3. Print/auto hint:
     a. Pre-flight — run Tesseract on a 25%-scale thumbnail of the first page.
        This takes ~200 ms.  If the thumbnail already fails the quality check,
        Tesseract is skipped for the whole document (no point running it at
        full resolution on something it clearly cannot read).
     b. Per-page full-resolution pass — Tesseract runs on each page
        individually.  Pages that pass quality checks keep their Tesseract
        text.  Pages that fail fall back to Claude Vision for that page
        only.  This avoids sending an entire multi-page document to Claude
        because one page happened to be difficult for Tesseract.

  4. Report method: "tesseract" | "claude" | "tesseract+claude".

Quality checks (all three must pass for Tesseract output to be accepted)
------------------------------------------------------------------------
  Word count        ≥ TESSERACT_MIN_WORDS (default 15)
                    Guards against blank or near-blank output.

  Alpha ratio       ≥ TESSERACT_MIN_ALPHA_RATIO (default 0.45)
                    Fraction of non-whitespace chars that are alphabetic.
                    Low ratio → symbol noise, severe garbling, non-text image.

  Mean word length  ≥ 2.0 characters/word
                    Guards against Tesseract outputting streams of single
                    characters separated by spaces (a common failure mode on
                    degraded scans).

Return value (when imported)
----------------------------
    {
        "text":    str,          # full text, pages joined by "\\n\\n---\\n\\n"
        "method":  str,          # "tesseract" | "claude" | "tesseract+claude"
        "pages":   list[dict],   # [{"page": 1, "method": "tesseract"}, ...]
        "source":  Path,
        "error":   str | None,
    }

CLI exit codes
--------------
    0  Success — text to stdout, method summary to stderr.
    1  Failure — error message to stderr.

Usage (CLI)
-----------
    python scripts/ocr.py path/to/scan.tiff
    python scripts/ocr.py path/to/scan.tiff --hint handwritten
    python scripts/ocr.py path/to/scanned.pdf --hint print

Usage (import)
--------------
    from scripts.ocr import ocr_file
    result = ocr_file(Path("scan.tiff"), hint="auto")
"""

import base64
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.config import (
    JPEG_QUALITY,
    OCR_CLAUDE_MODEL,
    PDF_OCR_DPI,
    TESSERACT_MIN_ALPHA_RATIO,
    TESSERACT_MIN_WORDS,
)

# Fraction of original image dimensions used for the pre-flight thumbnail.
# 0.25 (25%) produces a thumbnail in ~200 ms that is large enough to detect
# whether Tesseract can find text, while being small enough to be cheap.
_PREFLIGHT_SCALE = 0.25


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------

def _to_jpegs(image_path: Path, dest_dir: Path) -> list[Path]:
    """Convert a single image file (TIFF, PNG, JPEG) to per-page JPEG files.

    Multi-page TIFFs produce one JPEG per frame.  Single-page images produce
    one JPEG.  All output is written to dest_dir with names page_NNNN.jpg.
    """
    from PIL import Image

    img = Image.open(image_path)

    # n_frames is a TIFF-specific attribute; single-frame images lack it.
    try:
        n_frames = img.n_frames
    except AttributeError:
        n_frames = 1

    jpegs = []
    for i in range(n_frames):
        if n_frames > 1:
            img.seek(i)  # advance to the next TIFF frame
        frame = img.convert("RGB")  # ensure no alpha channel (JPEG doesn't support it)
        out = dest_dir / f"page_{i:04d}.jpg"
        frame.save(out, "JPEG", quality=JPEG_QUALITY)
        jpegs.append(out)

    return jpegs


def _pdf_to_jpegs(pdf_path: Path, dest_dir: Path) -> list[Path]:
    """Rasterise each page of a PDF to a JPEG file.

    Requires poppler (pdftoppm) to be installed.  DPI is controlled by
    PDF_OCR_DPI in config.py (default 300).
    """
    from pdf2image import convert_from_path

    pages = convert_from_path(str(pdf_path), dpi=PDF_OCR_DPI)
    jpegs = []
    for i, page in enumerate(pages):
        out = dest_dir / f"page_{i:04d}.jpg"
        page.save(out, "JPEG", quality=JPEG_QUALITY)
        jpegs.append(out)
    return jpegs


def _prepare_jpegs(filepath: Path, dest_dir: Path) -> list[Path]:
    """Dispatch to the appropriate image-preparation function based on extension."""
    return (
        _pdf_to_jpegs(filepath, dest_dir)
        if filepath.suffix.lower() == ".pdf"
        else _to_jpegs(filepath, dest_dir)
    )


def _make_thumbnail(jpeg_path: Path, dest_dir: Path) -> Path:
    """Create a small thumbnail of *jpeg_path* for the Tesseract pre-flight.

    The thumbnail is _PREFLIGHT_SCALE (25%) of the original dimensions.
    Lower resolution means Tesseract runs faster; the thumbnail is still
    large enough to detect whether a document contains readable text.
    """
    from PIL import Image

    img = Image.open(jpeg_path).convert("RGB")
    w, h = img.size
    thumb = img.resize(
        (max(1, int(w * _PREFLIGHT_SCALE)), max(1, int(h * _PREFLIGHT_SCALE))),
        Image.LANCZOS,
    )
    out = dest_dir / "preflight_thumb.jpg"
    # Use lower JPEG quality for the thumbnail — it's only used for OCR, not
    # for the final transcription.
    thumb.save(out, "JPEG", quality=75)
    return out


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------

def _tesseract_image(img) -> str:
    """Run Tesseract on a PIL Image object and return the raw text string.

    --oem 3  Use the default OCR engine (LSTM-based in Tesseract 4+).
    --psm 6  Assume a single uniform block of text.  Works well for most
             document pages; consider --psm 3 (fully automatic) if documents
             have complex multi-column layouts.
    """
    import pytesseract
    return pytesseract.image_to_string(img, config="--oem 3 --psm 6")


def _quality_ok(text: str) -> bool:
    """Return True if *text* looks like a real Tesseract transcription.

    Three independent checks are applied; all must pass:

    1. Word count — output must contain at least TESSERACT_MIN_WORDS tokens.
       Very short output almost always means Tesseract found no legible text.

    2. Alpha ratio — at least TESSERACT_MIN_ALPHA_RATIO of non-whitespace
       characters must be alphabetic.  A low ratio indicates heavy noise:
       random punctuation, box-drawing characters, or a non-text image.

    3. Mean word length — average word length must be ≥ 2.0 characters.
       A low mean catches the failure mode where Tesseract outputs a stream
       of single characters separated by spaces ("l e t t e r s") — these
       pass the word-count check but are clearly not real text.
    """
    words = text.split()
    if len(words) < TESSERACT_MIN_WORDS:
        return False

    non_ws = re.sub(r"\s", "", text)
    if not non_ws:
        return False

    alpha_ratio = sum(c.isalpha() for c in non_ws) / len(non_ws)
    if alpha_ratio < TESSERACT_MIN_ALPHA_RATIO:
        return False

    mean_word_len = sum(len(w) for w in words) / len(words)
    if mean_word_len < 2.0:
        return False

    return True


def _preflight_ok(jpegs: list[Path], dest_dir: Path) -> bool:
    """Run the Tesseract pre-flight check on a thumbnail of the first page.

    Returns True if Tesseract appears viable for this document (i.e. the
    thumbnail passes quality checks), False if Tesseract should be skipped
    entirely in favour of Claude Vision.

    Returns False on any import or runtime error so that missing Tesseract
    gracefully degrades to Claude Vision rather than crashing.
    """
    try:
        import pytesseract  # noqa: F401 — verify import before doing work
        from PIL import Image
    except ImportError:
        # pytesseract or Pillow not installed — cannot run Tesseract.
        print("  Pre-flight: pytesseract not available — skipping Tesseract.", file=sys.stderr)
        return False

    try:
        thumb_path = _make_thumbnail(jpegs[0], dest_dir)
        img = Image.open(thumb_path)
        text = _tesseract_image(img)

        # Calculate diagnostic values for the progress message.
        non_ws = text.replace(" ", "").replace("\n", "")
        alpha_ratio = (
            sum(c.isalpha() for c in non_ws) / len(non_ws) if non_ws else 0.0
        )
        ok = _quality_ok(text)
        print(
            f"  Pre-flight: {'PASS' if ok else 'FAIL'} "
            f"({len(text.split())} words, alpha={alpha_ratio:.2f})",
            file=sys.stderr,
        )
        return ok
    except Exception as exc:
        print(f"  Pre-flight error ({exc}) — skipping Tesseract.", file=sys.stderr)
        return False


def _tesseract_page(jpeg_path: Path) -> str:
    """Run Tesseract on a single full-resolution JPEG and return the text."""
    from PIL import Image
    return _tesseract_image(Image.open(jpeg_path))


# ---------------------------------------------------------------------------
# Claude Vision
# ---------------------------------------------------------------------------

# Prompts are kept terse to minimise token usage on the instruction side.
# The key instruction is "output the transcription only" — without it, Claude
# tends to add preamble ("Here is the transcription:") that pollutes the text.

_CLAUDE_PRINT_PROMPT = (
    "Transcribe all printed text in this document image exactly as it appears. "
    "Preserve paragraph breaks and visible structure (headings, lists, tables). "
    "Output the transcription only — no commentary."
)

_CLAUDE_HANDWRITTEN_PROMPT = (
    "Transcribe all handwritten text in this document image exactly as written. "
    "Preserve line breaks and layout where meaningful. "
    "Mark genuinely illegible passages as [illegible] and uncertain readings as [?word?]. "
    "Output the transcription only — no commentary."
)


def _claude_page(jpeg_path: Path, prompt: str) -> str:
    """Send one JPEG to Claude Vision and return the transcription.

    The image is base64-encoded and sent via the Anthropic messages API.
    Requires ANTHROPIC_API_KEY to be set in the environment.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Export it before running ocr.py with Claude Vision."
        )

    client = anthropic.Anthropic(api_key=api_key)
    # Read and encode the JPEG as base64 — the messages API requires this for
    # image content blocks.
    image_data = base64.standard_b64encode(jpeg_path.read_bytes()).decode("utf-8")

    msg = client.messages.create(
        model=OCR_CLAUDE_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return msg.content[0].text


# ---------------------------------------------------------------------------
# Per-page OCR with fallback
# ---------------------------------------------------------------------------

def _ocr_pages(
    jpegs: list[Path], hint: str, skip_tesseract: bool
) -> tuple[list[str], list[str]]:
    """OCR all pages and return parallel (texts, methods) lists.

    Each element of methods is either "tesseract" or "claude", corresponding
    to which engine produced the text at the same index in texts.

    Parameters
    ----------
    jpegs:
        Ordered list of per-page JPEG paths.
    hint:
        "handwritten" selects the handwriting prompt for Claude;
        anything else uses the print prompt.
    skip_tesseract:
        When True (pre-flight failed or handwritten hint), Claude Vision is
        used for every page without any Tesseract attempt.
    """
    prompt = (
        _CLAUDE_HANDWRITTEN_PROMPT if hint == "handwritten" else _CLAUDE_PRINT_PROMPT
    )
    texts: list[str]   = []
    methods: list[str] = []

    for i, jpeg in enumerate(jpegs, start=1):
        page_label = f"page {i}/{len(jpegs)}"

        if not skip_tesseract:
            tess_text = _tesseract_page(jpeg)
            if _quality_ok(tess_text):
                print(f"  {page_label}: Tesseract OK", file=sys.stderr)
                texts.append(tess_text)
                methods.append("tesseract")
                continue  # move to next page without calling Claude

            # This page failed Tesseract quality checks.
            word_count = len(tess_text.split())
            print(
                f"  {page_label}: Tesseract quality poor ({word_count} words) "
                "— Claude Vision.",
                file=sys.stderr,
            )

        # Reach here if skip_tesseract is True (pre-flight failed or
        # handwritten) or if this page's Tesseract result was unacceptable.
        print(f"  {page_label}: Claude Vision…", file=sys.stderr)
        texts.append(_claude_page(jpeg, prompt))
        methods.append("claude")

    return texts, methods


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def ocr_file(filepath: Path, hint: str = "auto") -> dict:
    """OCR *filepath* and return a result dict.

    Parameters
    ----------
    filepath:
        Path to the image or scanned PDF to process.
    hint:
        "print" or "auto" — try Tesseract first with Claude fallback per page.
        "handwritten"      — skip Tesseract; use Claude Vision for all pages.

    Returns
    -------
    dict with keys:
        text     str           Pages joined by "\\n\\n---\\n\\n".
        method   str           "tesseract" | "claude" | "tesseract+claude".
        pages    list[dict]    Per-page breakdown: [{"page": N, "method": ...}].
        source   Path          The input filepath.
        error    str | None    Error message on failure; None on success.
    """
    filepath = Path(filepath)
    result = {"text": "", "method": "", "pages": [], "source": filepath, "error": None}

    # Use a temporary directory for all intermediate JPEG files so they are
    # cleaned up automatically when the context manager exits, even on error.
    with tempfile.TemporaryDirectory(prefix="mwiki_ocr_") as tmp:
        tmp_dir = Path(tmp)

        # Step 1: convert the input file to a list of JPEG page images.
        try:
            print(f"  Preparing images from {filepath.name} …", file=sys.stderr)
            jpegs = _prepare_jpegs(filepath, tmp_dir)
            print(f"  {len(jpegs)} page(s) ready.", file=sys.stderr)
        except Exception as exc:
            result["error"] = f"Image preparation failed: {type(exc).__name__}: {exc}"
            return result

        # Step 2: decide whether to attempt Tesseract at all.
        if hint == "handwritten":
            print(
                "  Hint is 'handwritten' — using Claude Vision for all pages.",
                file=sys.stderr,
            )
            skip_tesseract = True
        else:
            print("  Running Tesseract pre-flight on thumbnail…", file=sys.stderr)
            skip_tesseract = not _preflight_ok(jpegs, tmp_dir)
            if skip_tesseract:
                print(
                    "  Pre-flight failed — skipping Tesseract, using Claude Vision.",
                    file=sys.stderr,
                )

        # Step 3: per-page OCR with per-page fallback.
        try:
            texts, methods = _ocr_pages(jpegs, hint, skip_tesseract)
        except Exception as exc:
            result["error"] = f"OCR failed: {type(exc).__name__}: {exc}"
            return result

        # Step 4: summarise which engine(s) were used.
        unique_methods = set(methods)
        if unique_methods == {"tesseract"}:
            method_label = "tesseract"
        elif unique_methods == {"claude"}:
            method_label = "claude"
        else:
            method_label = "tesseract+claude"

        result["text"]   = "\n\n---\n\n".join(texts)
        result["method"] = method_label
        result["pages"]  = [
            {"page": i + 1, "method": m} for i, m in enumerate(methods)
        ]

        tess_count  = methods.count("tesseract")
        claude_count = methods.count("claude")
        print(
            f"  Done: {tess_count} Tesseract page(s), {claude_count} Claude page(s). "
            f"Method: {method_label}",
            file=sys.stderr,
        )

    return result


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="OCR a scanned image or PDF to plain text.",
        epilog=(
            "Text is written to stdout. Progress and metadata are written to stderr. "
            "Exit 0 on success, 1 on failure."
        ),
    )
    parser.add_argument("filepath", type=Path, help="Image or scanned PDF to process")
    parser.add_argument(
        "--hint",
        choices=["print", "handwritten", "auto"],
        default="auto",
        help=(
            "Content type hint. "
            "'auto'/'print': Tesseract first, Claude fallback per page. "
            "'handwritten': Claude Vision for all pages (default: auto)"
        ),
    )
    args = parser.parse_args()

    if not args.filepath.exists():
        print(f"Error: file not found: {args.filepath}", file=sys.stderr)
        sys.exit(1)

    res = ocr_file(args.filepath, hint=args.hint)

    if res["error"]:
        print(f"OCR error: {res['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"Method: {res['method']}", file=sys.stderr)
    print(res["text"])
