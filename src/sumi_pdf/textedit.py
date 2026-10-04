"""Same-baseline text replacement + partial-op reconstruction, MIT stack.

pdfium finds the needle (exact char boxes) -> pypdf drops overlapping show-ops ->
reportlab re-inserts replacement + reconstructs the surviving chars of dropped ops
at their original positions (per-char, so spacing is preserved).
"""
from __future__ import annotations

import io

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from . import edit as E
from . import pdfio as P

_FONT = "SumiGothic"
_FONT_KEY = "ipaexg"


def ensure_font():
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        from . import fonts as F
        pdfmetrics.registerFont(TTFont(_FONT, F.bundled_path(f"{_FONT_KEY}.ttf")))


def _overlay(page_w, page_h, items):
    """items: (x_baseline, y_baseline, size, text, color)."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    ensure_font()
    t = c.beginText()
    for x, y, size, text, color in items:
        if not text:
            continue
        t.setFont(_FONT, size)
        t.setFillColorRGB(*color)
        t.setTextOrigin(x, y)
        t.textOut(text)
    c.drawText(t)
    c.save()
    return buf.getvalue()


def recon_items(chars: list[dict], metas: list[dict],
                rects: list[tuple]) -> list[tuple]:
    """Chars of dropped ops outside any rect, per-char at original positions."""
    items = []
    for m in metas:
        line = [c for c in chars
                if abs(c["y"] - m["y"]) < 1.5 and m["x"] - 1.5 <= c["x"] <= m["x"] + m["w"]]
        for r in rects:
            if not (m["x"] < r[2] and m["x"] + m["w"] > r[0] and
                    r[1] - 2.0 <= m["y"] <= r[3] + 2.0):
                continue
            for c in line:
                if r[0] - 0.5 <= c["x"] <= r[2] + 0.5:
                    continue  # inside the redaction/needle -> not restored
                items.append((c["x"], c["y"], m["size"], c["u"], m["color"]))
    return items


def replace(data: bytes, pno: int, needle: str, repl: str, size: float | None = None,
            color: tuple[float, float, float] = (0, 0, 0)) -> dict:
    """Replace all occurrences of needle on page pno at the same baseline."""
    occs = P.search(data, pno, needle)
    if not occs:
        return {"bytes": data, "replaced": 0, "dropped": 0}
    rects = [o.rect for o in occs]
    chars = P.page_chars(data, pno)
    data2, metas = E.remove_text_in_rects(data, pno, rects)
    pw, ph = P.page_size(data, pno)
    items: list = []
    for o in occs:  # the replacement itself
        sz = size or max(6.0, o.size * 0.92)
        items.append((o.origin[0], o.origin[1], sz, repl, color))
    items += recon_items(chars, metas, rects)  # surviving chars of dropped ops
    ov = _overlay(pw, ph, items)
    out = E.merge_overlay(data2, pno, ov)
    return {"bytes": out, "replaced": len(occs), "dropped": len(metas)}
