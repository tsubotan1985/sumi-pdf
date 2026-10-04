"""Text search -> redaction candidates (find -> check -> bulk redact).

``find_candidates`` locates every occurrence of a needle and returns one
candidate per hit: the exact charbox rectangle in PDF points (user space,
y up — the same coordinate space :mod:`sumi_pdf.redact` consumes, so a
candidate feeds straight into ``redact()`` / ``/api/redact-bulk``) plus a
context string with up to ``CONTEXT_CHARS`` characters on each side of the
match (前後20字) for the UI checklist.

Coordinates and context both come from the textpage character index space
(the same indices ``pdfio.page_chars`` reports), so rect and context can
never drift apart.
"""
from __future__ import annotations

from . import pdfio as P

CONTEXT_CHARS = 20  # 前後この字数を文脈として添える


def _context(tp, total: int, idx: int, cnt: int) -> str:
    """Match with up to CONTEXT_CHARS chars before/after (single-line)."""
    lo = max(0, idx - CONTEXT_CHARS)
    hi = min(total, idx + cnt + CONTEXT_CHARS)
    if hi <= lo:
        return ""
    raw = tp.get_text_range(lo, hi - lo)
    return raw.replace("\r", " ").replace("\n", " ")


def find_candidates(data: bytes, q: str, page: int | None = None) -> list[dict]:
    """Search ``q`` and return redaction candidates.

    Each candidate is ``{page, x0, y0, x1, y1, context}`` — the rect in PDF
    points (user space, y up), tight around the matched characters;
    ``context`` holds the match plus up to 20 chars on each side.

    ``page=None`` searches every page in order (ページ跨ぎ); ``page=int``
    restricts to that page and raises ``ValueError`` when out of range.
    Empty ``q`` returns ``[]``.
    """
    if not q:
        return []
    d = P._doc(data)
    try:
        n = len(d)
        if page is not None:
            if not 0 <= page < n:
                raise ValueError(f"page out of range: {page} (pages={n})")
            pnos: list[int] | range = [page]
        else:
            pnos = range(n)
        out: list[dict] = []
        for pno in pnos:
            tp = d[pno].get_textpage()
            total = tp.count_chars()
            s = tp.search(q, match_case=True)
            while True:
                res = s.get_next()
                if not res:
                    break
                idx, cnt = res
                boxes = [tp.get_charbox(i) for i in range(idx, idx + cnt)]
                out.append({
                    "page": pno,
                    "x0": min(b[0] for b in boxes),
                    "y0": min(b[1] for b in boxes),
                    "x1": max(b[2] for b in boxes),
                    "y1": max(b[3] for b in boxes),
                    "context": _context(tp, total, idx, cnt),
                })
        return out
    finally:
        d.close()
