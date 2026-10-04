"""Page operations via pypdf (BSD-3): rotate / delete / extract / merge / insert."""
from __future__ import annotations

import io
import os

from pypdf import PdfReader, PdfWriter


def _w(data: bytes | None = None, path: str | None = None) -> PdfWriter:
    w = PdfWriter()
    if data is not None:
        w.append(PdfReader(io.BytesIO(data)))
    elif path:
        w.append(path)
    return w


def rotate(data: bytes, pno: int, deg: int) -> bytes:
    """Rotate page pno by deg (90/180/270, cumulative)."""
    w = _w(data)
    w.pages[pno].rotate(deg % 360)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def delete_pages(data: bytes, pnos: list[int]) -> bytes:
    keep = [i for i in range(len(PdfReader(io.BytesIO(data)).pages)) if i not in set(pnos)]
    return extract_pages(data, keep)


def extract_pages(data: bytes, pnos: list[int]) -> bytes:
    r = PdfReader(io.BytesIO(data))
    w = PdfWriter()
    for i in pnos:
        w.add_page(r.pages[i])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def insert_pdf(data: bytes, other_path: str, at: int | None = None) -> bytes:
    """Append (or insert at index) all pages of other_path."""
    w = _w(data)
    if at is None or at >= len(w.pages):
        w.append(other_path)
    else:
        r = PdfReader(other_path)
        for k, p in enumerate(r.pages):
            w.insert_page(p, at + k)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def merge_pdfs(paths: list[str], out: str) -> dict:
    w = PdfWriter()
    for p in paths:
        w.append(p)
    with open(out, "wb") as fh:
        w.write(fh)
    return {"out": out, "bytes": os.path.getsize(out), "pages": len(PdfReader(out).pages)}


def split(data: bytes, out_dir: str, base: str) -> list[str]:
    """One file per page -> out_dir/base-0001.pdf ..."""
    r = PdfReader(io.BytesIO(data))
    outs = []
    for i, p in enumerate(r.pages):
        w = PdfWriter()
        w.add_page(p)
        op = os.path.join(out_dir, f"{base}-{i + 1:04d}.pdf")
        with open(op, "wb") as fh:
            w.write(fh)
        outs.append(op)
    return outs
