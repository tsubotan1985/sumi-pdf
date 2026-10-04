"""PDF I/O via pypdfium2 (Apache-2.0/BSD) — render, text, search. MIT stack part 1."""
from __future__ import annotations

import ctypes
from typing import NamedTuple

import pypdfium2 as pdfium
from pypdfium2 import raw as _R


def page_chars(data: bytes, pno: int) -> list[dict]:
    """All chars of the page: {x, y (baseline origin), u (unicode), i (index)}."""
    import ctypes
    d = _doc(data)
    try:
        page = d[pno]
        tp = page.get_textpage()
        n = tp.count_chars()
        out = []
        for i in range(n):
            ox = ctypes.c_double()
            oy = ctypes.c_double()
            _R.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(ox), ctypes.byref(oy))
            u = _R.FPDFText_GetUnicode(tp.raw, i)
            out.append({"i": i, "x": ox.value, "y": oy.value,
                        "u": chr(u) if u and u > 0 else ""})
        return out
    finally:
        d.close()


def _doc(data: bytes, password: str | None = None) -> pdfium.PdfDocument:
    return pdfium.PdfDocument(data, password=password)


def page_count(data: bytes) -> int:
    return len(_doc(data))


def page_size(data: bytes, pno: int) -> tuple[float, float]:
    d = _doc(data)
    try:
        return d[pno].get_size()
    finally:
        d.close()


def render_png(data: bytes, pno: int, dpi: int = 110, password: str | None = None) -> bytes:
    import io

    d = _doc(data, password)
    try:
        bmp = d[pno].render(scale=dpi / 72.0)
        pil = bmp.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        d.close()


def render_pil(data: bytes, pno: int, dpi: int = 110):
    d = _doc(data)
    try:
        return d[pno].render(scale=dpi / 72.0).to_pil()
    finally:
        d.close()


def extract_text(data: bytes, pno: int) -> str:
    d = _doc(data)
    try:
        return d[pno].get_textpage().get_text_range()
    finally:
        d.close()


class Occurrence(NamedTuple):
    rect: tuple[float, float, float, float]  # x0, y0, x1, y1 (user space, y up)
    origin: tuple[float, float]              # baseline origin of first char
    size: float                              # approx glyph size


def search(data: bytes, pno: int, needle: str) -> list[Occurrence]:
    """Find all occurrences of needle on the page (exact char boxes)."""
    if not needle:
        return []
    d = _doc(data)
    try:
        page = d[pno]
        tp = page.get_textpage()
        out: list[Occurrence] = []
        s = tp.search(needle, match_case=True)
        while True:
            res = s.get_next()
            if not res:
                break
            idx, cnt = res
            boxes = [tp.get_charbox(i) for i in range(idx, idx + cnt)]
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            ox = ctypes.c_double()
            oy = ctypes.c_double()
            _R.FPDFText_GetCharOrigin(tp.raw, idx, ctypes.byref(ox), ctypes.byref(oy))
            h = y1 - y0
            out.append(Occurrence((x0, y0, x1, y1), (ox.value, oy.value), h if h > 0 else 12.0))
        return out
    finally:
        d.close()


def font_inventory(data: bytes) -> list[dict]:
    """Fonts per document via pypdf: name, embedded?, page count of use."""
    from pypdf import PdfReader
    import io

    r = PdfReader(io.BytesIO(data))
    seen: dict[str, dict] = {}
    for page in r.pages:
        res = page.get("/Resources") or {}
        fonts = res.get("/Font") or {}
        if hasattr(fonts, "get_object"):
            fonts = fonts.get_object()
        for _k, ref in (fonts.items() if hasattr(fonts, "items") else []):
            try:
                f = ref.get_object()
            except Exception:
                continue
            name = str(f.get("/BaseFont", "?")).lstrip("/")
            sub = str(f.get("/Subtype", "?"))
            fd = f.get("/FontDescriptor")
            embedded = False
            if fd is not None:
                fd = fd.get_object()
                embedded = any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3"))
            elif f.get("/DescendantFonts") is not None:
                try:
                    dfd = f["/DescendantFonts"][0].get_object().get("/FontDescriptor")
                    if dfd is not None:
                        dfd = dfd.get_object()
                        embedded = any(k in dfd for k in ("/FontFile", "/FontFile2", "/FontFile3"))
                except Exception:
                    pass
            e = seen.setdefault(name, {"name": name, "subtype": sub, "embedded": embedded, "pages": 0})
            e["pages"] += 1
    return sorted(seen.values(), key=lambda x: -x["pages"])
