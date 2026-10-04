"""True redaction, MIT stack: pypdf op removal + background fill + char reconstruction."""
from __future__ import annotations

from statistics import median

from . import edit as E
from . import pdfio as P
from .textedit import _overlay, recon_items


def sample_bg(data: bytes, pno: int, rect: tuple[float, float, float, float],
              dpi: int = 96) -> tuple[float, float, float]:
    """Median color of a ring around rect (PDF points, y up) -> (r,g,b in 0..1)."""
    pil = P.render_pil(data, pno, dpi).convert("RGB")
    w, h = pil.size
    pw, ph = P.page_size(data, pno)
    s = dpi / 72.0
    x0, y0, x1, y1 = rect
    ix0, ix1 = int(min(x0, x1) * s), int(max(x0, x1) * s)
    iy_top = int((ph - max(y0, y1)) * s)
    iy_bot = int((ph - min(y0, y1)) * s)
    band = max(3, int(4 * s))
    ring = []
    for ix in range(max(0, ix0 - band), min(w, ix1 + band), max(1, band // 2)):
        for iy in list(range(max(0, iy_top - band), max(0, iy_top))) + \
                  list(range(iy_bot, min(h, iy_bot + band))):
            if 0 <= iy < h and 0 <= ix < w:
                ring.append(pil.getpixel((ix, iy)))
    if not ring:
        return (1.0, 1.0, 1.0)
    return (round(median(c[0] for c in ring) / 255, 3),
            round(median(c[1] for c in ring) / 255, 3),
            round(median(c[2] for c in ring) / 255, 3))


def redact(data: bytes, pno: int, rects: list[tuple[float, float, float, float]],
           match_bg: bool = True, fill: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> dict:
    """True redaction on one page:
    1) drop text-showing ops overlapping each rect (text data gone)
    2) reconstruct surviving chars of partially-covered ops at original positions
    3) paint the rect with background color (covers images/pixels visually)
    Image *pixel* surgery (data removal inside images) is planned v0.6.
    """
    if not rects:
        return {"bytes": data, "dropped": 0, "filled": 0, "bg": None}
    rects = [tuple(r) for r in rects]
    chars = P.page_chars(data, pno)
    data2, metas = E.remove_text_in_rects(data, pno, rects)
    items = recon_items(chars, metas, rects)
    if items:
        pw, ph = P.page_size(data, pno)
        data2 = E.merge_overlay(data2, pno, _overlay(pw, ph, items))
    colors = [(r, sample_bg(data, pno, r) if match_bg else fill) for r in rects]
    out = E.append_fill_rects(data2, pno, colors)
    return {"bytes": out, "dropped": len(metas), "filled": len(colors),
            "bg": colors[0][1] if colors else None}
