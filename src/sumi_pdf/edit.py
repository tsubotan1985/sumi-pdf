"""Content-stream editing via pypdf (BSD-3) — true text removal, fill rects, overlays.

Text removal = drop text-showing operators whose render origin (CTM x Tlm, tracked
with a text-state machine) falls inside a target rect. Works without decoding the
string bytes, so CID/CJK encodings are handled the same as simple fonts.

pypdf ContentStream layout: operations = [(operands: list[generic], operator: bytes)]

Known v1 limits: text inside Form XObjects is not touched; a show-op whose estimated
width intersects the rect is dropped whole (op granularity, like MARIN's splitter).
"""
from __future__ import annotations

import io
import os
import math

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, ByteStringObject, ContentStream, FloatObject,
                           NameObject, NumberObject, TextStringObject)

# ---- 2x3 affine -----------------------------------------------------------

def _ident():
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mul(m, n):
    """Return m x n (apply m first, then n)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _op_name(entry) -> str:
    op = entry[1]
    return op.decode("latin-1") if isinstance(op, bytes) else str(op)


def _op_args(entry) -> list:
    return entry[0]


# ---- text-state machine ---------------------------------------------------

_SHOW = {"Tj", "'", '"', "TJ"}


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _est_width(name, args, tsize):
    """Generous upper-bound width so mid-line needles still hit (recon restores)."""
    try:
        if name == "TJ" and args and isinstance(args[0], ArrayObject):
            n = sum(_oplen(e) for e in args[0])
        else:
            n = _oplen(args[0]) if args else 0
    except Exception:
        n = 8
    return n * max(tsize, 1.0) * 1.05


def _oplen(e) -> int:
    if isinstance(e, bytes):
        return len(e)
    if isinstance(e, str):
        return len(e.encode("latin-1", "ignore")) or len(e)
    return len(str(e))


def _collect_and_drop(page_ops: list, rects: list[tuple], pad: float = 1.0):
    """Drop show ops whose origin/estimated span lies in any rect.

    Returns (ops, dropped_meta): dropped_meta = [{x, y, size, color, w}, ...]
    for reconstruction of text around the redaction (see textedit.recon_items).
    """
    ctm = _ident()
    tlm = _ident()
    leading = 0.0
    tsize = 0.0
    color = (0.0, 0.0, 0.0)
    in_bt = False
    stack: list = []
    dropped: list[dict] = []
    out: list = []
    for entry in page_ops:
        args, name = _op_args(entry), _op_name(entry)
        if name == "q":
            stack.append(ctm)
            out.append(entry)
            continue
        if name == "Q":
            ctm = stack.pop() if stack else _ident()
            out.append(entry)
            continue
        if name == "cm" and len(args) == 6:
            ctm = _mul(tuple(_num(a) for a in args), ctm)
            out.append(entry)
            continue
        if name == "rg" and len(args) == 3:
            color = tuple(_num(a) for a in args)
        elif name == "g" and len(args) == 1:
            v = _num(args[0])
            color = (v, v, v)
        if name == "BT":
            in_bt = True
            tlm = _ident()
            out.append(entry)
            continue
        if name == "ET":
            in_bt = False
            out.append(entry)
            continue
        if name == "Tm" and len(args) == 6:
            tlm = tuple(_num(a) for a in args)
        elif name == "Td" and len(args) == 2:
            tlm = _mul((1, 0, 0, 1, _num(args[0]), _num(args[1])), tlm)
        elif name == "TD" and len(args) == 2:
            leading = -_num(args[1])
            tlm = _mul((1, 0, 0, 1, _num(args[0]), _num(args[1])), tlm)
        elif name == "TL" and len(args) == 1:
            leading = _num(args[0])
        elif name == "T*":
            tlm = _mul((1, 0, 0, 1, 0, -leading), tlm)
        elif name == "Tf" and len(args) == 2:
            tsize = abs(_num(args[1]))
        elif name in _SHOW and in_bt:
            ex, ey = _apply(_mul(ctm, tlm), 0.0, 0.0)
            w = _est_width(name, args, tsize)
            for (rx0, ry0, rx1, ry1) in rects:
                if ex < (rx1 + pad) and (ex + max(w, 0.0)) > (rx0 - pad) and \
                        (ry0 - 2.0) <= ey <= (ry1 + 2.0):
                    dropped.append({"x": ex, "y": ey, "size": tsize,
                                    "color": color, "w": w})
                    break
            else:
                out.append(entry)
                continue
            continue  # drop the show op
        out.append(entry)
    return out, dropped


def _edit_page(data: bytes, pno: int):
    """Open writer-attached page + parsed content stream. Returns (writer, page, cs)."""
    w = PdfWriter(clone_from=io.BytesIO(data))
    page = w.pages[pno]
    cs = ContentStream(page.get_contents(), w)
    return w, page, cs


def _write(w: PdfWriter) -> bytes:
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def remove_text_in_rects(data: bytes, pno: int, rects: list[tuple]) -> tuple[bytes, list]:
    """Remove text-showing ops overlapping rects. Returns (new_bytes, dropped_meta)."""
    w, page, cs = _edit_page(data, pno)
    ops, metas = _collect_and_drop(cs.operations, rects)
    cs.operations = ops
    page.replace_contents(cs)
    return _write(w), metas


def append_fill_rects(data: bytes, pno: int, rects_colors: list) -> bytes:
    """Append filled rects ((x0,y0,x1,y1),(r,g,b)) on top of the page content."""
    w, page, cs = _edit_page(data, pno)
    add: list = []
    for (x0, y0, x1, y1), (cr, cg, cb) in rects_colors:
        add.append(([FloatObject(min(x0, x1)), FloatObject(min(y0, y1)),
                     FloatObject(abs(x1 - x0)), FloatObject(abs(y1 - y0))], b"re"))
        add.append(([FloatObject(cr), FloatObject(cg), FloatObject(cb)], b"rg"))
        add.append(([], b"f"))
    # draw AFTER existing content (on top), inside a balanced q/Q
    cs.operations = list(cs.operations) + [([], b"q")] + add + [([], b"Q")]
    page.replace_contents(cs)
    return _write(w)


def merge_overlay(data: bytes, pno: int, overlay_pdf_bytes: bytes) -> bytes:
    """Stamp overlay page (same mediabox) onto page pno (reportlab output)."""
    r = PdfReader(io.BytesIO(data))
    ov = PdfReader(io.BytesIO(overlay_pdf_bytes))
    w = PdfWriter()
    w.append(r)
    w.pages[pno].merge_page(ov.pages[0])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def save_optimized(data: bytes, out_path: str) -> int:
    r = PdfReader(io.BytesIO(data))
    w = PdfWriter()
    w.append(r)
    try:
        w.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    except Exception:
        pass
    with open(out_path, "wb") as fh:
        w.write(fh)
    return os.path.getsize(out_path)
