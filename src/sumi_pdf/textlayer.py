"""Invisible OCR text layer -> searchable PDF (reportlab render_mode 3, MIT stack)."""
from __future__ import annotations

import io

from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from . import edit as E

_FONT = "SumiGothic"


def _ensure():
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        from . import fonts as F
        pdfmetrics.registerFont(TTFont(_FONT, F.bundled_path("ipaexg.ttf")))


def add_layer_doc(data: bytes, pno: int, words: list[dict]) -> bytes:
    """words: tesseract TSV style {bbox:[l,t,r,b]} (top-down pt) or {x,y,h} (baseline
    y-up). Returns new bytes with an invisible text layer on page pno."""
    if not words:
        return data
    from . import pdfio as P
    pw, ph = P.page_size(data, pno)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    _ensure()
    t = c.beginText()
    t.setTextRenderMode(3)
    for w in words:
        if "bbox" in w:
            l, tt, r, b = w["bbox"]
            h = max(4.0, b - tt)
            x, y = l, ph - (tt + 0.82 * h)  # approx baseline
        else:
            x, y = float(w["x"]), float(w["y"])
            h = max(4.0, min(float(w.get("h", 8.0)), 72.0))
        t.setFont(_FONT, h)
        t.setTextOrigin(x, y)
        t.textOut(w["text"])
    c.drawText(t)
    c.save()
    return E.merge_overlay(data, pno, buf.getvalue())
