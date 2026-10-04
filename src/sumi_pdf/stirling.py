"""Stirling-parity utilities: compress / watermark / page numbers / metadata / sanitize."""
from __future__ import annotations

import io
import os

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from . import edit as E
from . import pdfio as P


# ---------------- compress ----------------

def compress(data: bytes, out_path: str, image_quality: int = 70,
             grayscale: bool = False) -> dict:
    """Recompress: rewrite stream + re-encode embedded images (DCT quality).

    Returns {out, before, after, ratio}.
    """
    from PIL import Image

    before = len(data)
    r = PdfReader(io.BytesIO(data))
    w = PdfWriter()
    w.append(r)
    # re-encode images
    for page in w.pages:
        res = page.get("/Resources")
        if not res:
            continue
        res = res.get_object()
        xo = res.get("/XObject")
        if not xo:
            continue
        xo = xo.get_object()
        for name, ref in list(xo.items()):
            obj = ref.get_object()
            if obj.get("/Subtype") != "/Image":
                continue
            try:
                pil = Image.open(io.BytesIO(obj.get_data()))
                pil.load()
            except Exception:
                continue
            if grayscale:
                pil = pil.convert("L")
            elif pil.mode not in ("RGB", "L"):
                pil = pil.convert("RGB")
            buf = io.BytesIO()
            if grayscale or pil.mode == "L":
                pil.save(buf, format="JPEG", quality=image_quality)
                filt = "/DCTDecode"
                cs = "/DeviceGray"
            elif obj.get("/Filter") == "/DCTDecode":
                pil.save(buf, format="JPEG", quality=image_quality)
                filt = "/DCTDecode"
                cs = "/DeviceRGB"
            else:
                pil.save(buf, format="PNG", optimize=True)
                filt = "/FlateDecode"
                cs = "/DeviceRGB" if pil.mode == "RGB" else "/DeviceGray"
            obj[NameObject("/Filter")] = NameObject("/FlateDecode")
            obj[NameObject("/ColorSpace")] = NameObject(cs)
            obj[NameObject("/BitsPerComponent")] = __import__("pypdf").generic.NumberObject(8)
            obj.set_data(pil.tobytes())  # pypdf re-applies Flate itself
    try:
        w.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    except Exception:
        pass
    with open(out_path, "wb") as fh:
        w.write(fh)
    after = os.path.getsize(out_path)
    return {"out": out_path, "before": before, "after": after,
            "ratio": round(after / before, 3) if before else 1.0}


# ---------------- watermark ----------------

def watermark(data: bytes, pno: int, text: str, opacity: float = 0.15,
              angle: int = 45, size: float = 60.0,
              color: tuple[float, float, float] = (0.6, 0.6, 0.6)) -> bytes:
    """Overlay a diagonal text watermark on page pno."""
    from reportlab.lib.colors import Color
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rl_canvas

    from . import fonts as F
    pw, ph = P.page_size(data, pno)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    if "SumiGothic" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("SumiGothic", F.bundled_path("ipaexg.ttf")))
    c.saveState()
    c.translate(pw / 2, ph / 2)
    c.rotate(angle)
    c.setFont("SumiGothic", size)
    c.setFillColor(Color(*color, alpha=opacity))
    c.drawCentredString(0, -size / 3, text)
    c.restoreState()
    c.save()
    return E.merge_overlay(data, pno, buf.getvalue())


# ---------------- page numbers ----------------

def page_numbers(data: bytes, out_path: str, fmt: str = "{n} / {total}",
                 pos: str = "bottom-center", size: float = 10.0,
                 start: int = 1) -> dict:
    """Stamp page numbers on every page (standard font — no embedding)."""
    from reportlab.pdfgen import canvas as rl_canvas

    n_pages = P.page_count(data)
    r = PdfReader(io.BytesIO(data))
    w = PdfWriter()
    w.append(r)
    for i in range(n_pages):
        pw, ph = P.page_size(data, i)
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
        c.setFont("Helvetica", size)
        c.setFillColorRGB(0.2, 0.2, 0.2)
        label = fmt.format(n=i + start, total=n_pages + start - 1)
        if pos == "bottom-center":
            c.drawCentredString(pw / 2, 24, label)
        elif pos == "bottom-right":
            c.drawRightString(pw - 40, 24, label)
        elif pos == "top-right":
            c.drawRightString(pw - 40, ph - 30, label)
        else:  # top-left
            c.drawString(40, ph - 30, label)
        c.save()
        w.pages[i].merge_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
    with open(out_path, "wb") as fh:
        w.write(fh)
    return {"out": out_path, "pages": n_pages, "bytes": os.path.getsize(out_path)}


# ---------------- metadata ----------------

def get_metadata(data: bytes) -> dict:
    r = PdfReader(io.BytesIO(data))
    md = r.metadata or {}
    return {k.lstrip("/"): str(v) for k, v in dict(md).items()}


def set_metadata(data: bytes, out_path: str, **kv) -> dict:
    """Set /Title /Author /Subject /Keywords /Creator /Producer. Delete: value=None."""
    w = PdfWriter(clone_from=io.BytesIO(data))
    w.add_metadata({("/" + k): ("" if v is None else str(v)) for k, v in kv.items()})
    with open(out_path, "wb") as fh:
        w.write(fh)
    return {"out": out_path, "bytes": os.path.getsize(out_path)}


# ---------------- sanitize (Stirling parity) ----------------

def sanitize(data: bytes, out_path: str, *, remove_js: bool = True,
             remove_embedded_files: bool = True, remove_metadata: bool = True,
             remove_links: bool = False, remove_annotations: bool = False) -> dict:
    """Strip: JavaScript, embedded files, metadata, (optionally) links/annotations.

    Also disables all document actions. Returns counts.
    """
    r = PdfReader(io.BytesIO(data))
    w = PdfWriter()
    w.append(r)  # NOTE: append() copies a minimal catalog — JS/AA/OpenAction do
    #            not survive it; count them from the source reader instead.
    counts = {"js": 0, "files": 0, "meta": 0, "links": 0, "annots": 0}

    root = w._root_object
    src_root = r.trailer["/Root"]
    # 1) JavaScript + open actions (counted from source; writer drops them)
    if remove_js:
        for k in ("/JavaScript", "/AA", "/OpenAction"):
            if k in src_root:
                counts["js"] += 1
        if "/JavaScript" in root:
            del root["/JavaScript"]
        if "/AA" in root:
            del root["/AA"]
        if "/OpenAction" in root:
            del root["/OpenAction"]
    # 2) embedded files
    if remove_embedded_files:
        src_names = src_root.get("/Names")
        if src_names:
            src_names = src_names.get_object()
            if "/EmbeddedFiles" in src_names:
                counts["files"] += 1
        names = root.get("/Names")
        if names:
            names = names.get_object()
            if "/EmbeddedFiles" in names:
                del names["/EmbeddedFiles"]
    # 3) metadata
    if remove_metadata:
        if "/Metadata" in root:
            del root["/Metadata"]; counts["meta"] += 1
        if "/Info" in root:
            del root["/Info"]; counts["meta"] += 1
        if "/Metadata" in src_root:
            counts["meta"] += 1
        w.add_metadata({"/Producer": "SUMIPDF", "/Creator": "SUMIPDF"})
    # 4) per-page: links / annotations / js in annotations
    for page in w.pages:
        if remove_annotations:
            if "/Annots" in page:
                del page["/Annots"]; counts["annots"] += 1
        elif remove_links:
            annots = page.get("/Annots")
            if not annots:
                continue
            annots = annots.get_object()
            keep = []
            for a in annots:
                try:
                    ao = a.get_object()
                    if ao.get("/Subtype") == "/Link":
                        counts["links"] += 1
                        continue
                except Exception:
                    pass
                keep.append(a)
            if len(keep) != len(annots):
                page[NameObject("/Annots")] = keep
        if remove_js and "/AA" in page:
            del page["/AA"]
    with open(out_path, "wb") as fh:
        w.write(fh)
    return {"out": out_path, "bytes": os.path.getsize(out_path), **counts}
