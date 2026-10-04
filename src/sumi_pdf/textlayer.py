"""Invisible OCR text layer -> searchable PDF (render_mode 3)."""
from __future__ import annotations

import pymupdf as fitz

from . import fonts as F


def add_searchable_layer(page: fitz.Page, words: list[dict],
                         fontfile: str | None = None) -> int:
    """Insert invisible (render_mode=3) OCR words with PDF-pt bboxes.

    words: [{"text": str, "bbox": [x0,y0,x1,y1]}, ...]  (from ocr.parse_tsv)
    Returns count inserted. Text stays extractable/searchable, invisible on render.
    """
    ff = fontfile or F.default_font("gothic")
    cnt = 0
    for w in words:
        r = fitz.Rect(w["bbox"])
        if r.is_empty or r.width <= 0 or r.height <= 0:
            continue
        if not page.rect.contains(fitz.Point(r.x0, r.y0)):
            continue
        size = max(4.0, min(r.height, 72.0))
        try:
            page.insert_text(fitz.Point(r.x0, r.y1), w["text"], fontsize=size,
                             fontname="SumiOCR", fontfile=ff, render_mode=3)
            cnt += 1
        except Exception:
            continue
    return cnt


def add_layer_doc(doc: fitz.Document, page_no: int, words: list[dict],
                  fontfile: str | None = None) -> int:
    return add_searchable_layer(doc[page_no], words, fontfile)
