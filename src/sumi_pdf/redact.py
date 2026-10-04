"""True redaction: remove text data, image pixels and (optionally) vector art."""
from __future__ import annotations

import pymupdf as fitz

IMAGE_MODES = {
    "none": fitz.PDF_REDACT_IMAGE_NONE,
    "pixels": fitz.PDF_REDACT_IMAGE_PIXELS,
    "remove": fitz.PDF_REDACT_IMAGE_REMOVE,
}
GRAPHICS_MODES = {
    "none": fitz.PDF_REDACT_LINE_ART_NONE,
    "if_covered": fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
    "if_touched": fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
}


def sample_bg(page: fitz.Page, rect, dpi: int = 72):
    """Sample the color under a rect (center pixel) to blend the redaction fill."""
    r = fitz.Rect(rect) & page.rect
    if r.is_empty:
        return (0, 0, 0)
    pm = page.get_pixmap(dpi=dpi, clip=r)
    return tuple(pm.pixel(pm.width // 2, pm.height // 2)[:3])


def redact(page: fitz.Page, rects, match_bg: bool = True, fill=(0, 0, 0),
           images: str = "pixels", graphics: str = "none") -> list[dict]:
    """Apply true redaction on `rects` (PDF user-space coords).

    Default: text removed, image pixels wiped, vector art kept, fill = background color.
    Returns applied rect/fill info.
    """
    infos = []
    for r in rects:
        rr = fitz.Rect(r)
        if rr.is_empty or not fitz.Rect(rr).intersects(page.rect):
            continue
        f = list(sample_bg(page, rr)) if match_bg else list(fill)
        annot = page.add_redact_annot(rr, fill=f)
        infos.append({"rect": [round(v, 2) for v in rr], "fill": [int(c) for c in f],
                      "annot": annot})
    page.apply_redactions(
        images=IMAGE_MODES.get(images, fitz.PDF_REDACT_IMAGE_PIXELS),
        graphics=GRAPHICS_MODES.get(graphics, fitz.PDF_REDACT_LINE_ART_NONE),
    )
    return [{k: v for k, v in i.items() if k != "annot"} for i in infos]


def redact_doc(doc: fitz.Document, page_no: int, rects, **kw) -> list[dict]:
    return redact(doc[page_no], rects, **kw)
