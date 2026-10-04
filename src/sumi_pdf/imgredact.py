"""Image pixel surgery: remove pixels inside rects from embedded images (pypdf).

True data removal — the pixels inside the rect are blanked in the image XObject
itself, so no recovery is possible from the file. Strategy per page:
1) find image XObjects whose placement intersects a rect (via content stream cm
   tracking in edit.py's state machine -> here: pypdf image extraction with bbox)
2) decode with Pillow, paint bg color inside the mapped rect, re-encode in place.

Simplified v1: uses pdfium to locate image placement rectangles per page
(FPDFPage_GetObjects via pypdfium2 raw), maps page rect -> image pixel rect.
"""
from __future__ import annotations

import ctypes
import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject,
                           NumberObject)

from PIL import Image

from . import edit as E


def _image_placements(data: bytes, pno: int) -> list[dict]:
    """[{name, page_rect}] for images drawn on the page (via pdfium page objects)."""
    from pypdfium2 import PdfDocument
    from pypdfium2 import raw as R
    d = PdfDocument(data)
    try:
        page = d[pno]
        out = []
        count = R.FPDFPage_CountObjects(page.raw)
        for i in range(count):
            obj = R.FPDFPage_GetObject(page.raw, i)
            t = R.FPDFPageObj_GetType(obj)
            if t != R.FPDF_PAGEOBJ_IMAGE:  # 3
                continue
            l = ctypes.c_float(); b = ctypes.c_float()
            r = ctypes.c_float(); t_ = ctypes.c_float()
            ok = R.FPDFPageObj_GetBounds(obj, ctypes.byref(l), ctypes.byref(b),
                                         ctypes.byref(r), ctypes.byref(t_))
            if not ok:
                continue
            # name via pypdf resources match later; record bounds
            out.append({"rect": (l.value, b.value, r.value, t_.value), "obj": i})
        return out
    finally:
        d.close()


def _res_images(page) -> dict:
    res = page.get("/Resources")
    if not res:
        return {}
    res = res.get_object()
    xo = res.get("/XObject")
    if not xo:
        return {}
    xo = xo.get_object()
    return {str(k): v.get_object() for k, v in xo.items()
            if v.get_object().get("/Subtype") == "/Image"}


def _image_placement_names(data: bytes, pno: int) -> list[tuple[str, tuple]]:
    """[(xobj_name, page_rect)] matching pdfium placements order-agnostically."""
    from pypdfium2 import PdfDocument
    from pypdfium2 import raw as R
    imgs = _image_placements(data, pno)
    if not imgs:
        return []
    r = PdfReader(io.BytesIO(data))
    names = list(_res_images(r.pages[pno]).keys())
    # pdfium has no direct name; heuristic: one image -> all placements use it.
    # multi-image pages: map by draw order (pdfium order == content order for images)
    if len(names) == 1:
        return [(names[0], im["rect"]) for im in imgs]
    # try mark-content/name via raw API (FPDFPageObjMark_GetName) fallback: order
    out = []
    for k, im in enumerate(imgs):
        out.append((names[k % len(names)], im["rect"]))
    return out


def _decode_image(xobj) -> Image.Image | None:
    """Decode an image XObject into PIL. Handles raw-sample streams (Flate after
    pypdf get_data) and JPEG/DCT; falls back to reconstructing from raw samples."""
    import io as _io

    filt = xobj.get("/Filter")
    fl = [str(f) for f in (filt if isinstance(filt, ArrayObject) else ([filt] if filt else []))]
    w = int(xobj.get("/Width", 0))
    h = int(xobj.get("/Height", 0))
    if not w or not h:
        return None
    try:
        raw = xobj.get_data()
        if "/DCTDecode" in fl:
            return Image.open(_io.BytesIO(raw))
        try:
            im = Image.open(_io.BytesIO(raw))
            im.load()
            return im
        except Exception:
            pass
        # raw sample stream: 8bpc DeviceRGB or DeviceGray
        bpc = int(xobj.get("/BitsPerComponent", 8))
        cs = str(xobj.get("/ColorSpace", "/DeviceRGB"))
        if bpc != 8:
            return None
        if "/DeviceGray" in cs or cs == "/G":
            if len(raw) >= w * h:
                return Image.frombytes("L", (w, h), raw[: w * h])
        else:
            if len(raw) >= w * h * 3:
                return Image.frombytes("RGB", (w, h), raw[: w * h * 3])
        return None
    except Exception:
        return None


def remove_pixels(data: bytes, pno: int, rects: list[tuple],
                  fill=(255, 255, 255)) -> dict:
    """Blank pixels inside page rects in the page's embedded images (destructive).

    fill: RGB 0-255 painted into the image pixels (use sampled bg for invisibility).
    Returns {edited: image names, rects: n}.
    """
    from PIL import Image

    placements = _image_placement_names(data, pno)
    if not placements:
        return {"edited": [], "rects": len(rects)}

    w = PdfWriter(clone_from=io.BytesIO(data))
    page = w.pages[pno]
    images = _res_images(page)
    edited = []
    for name, prect in placements:
        xobj = images.get(name)
        if xobj is None:
            continue
        pil = _decode_image(xobj)
        if pil is None:
            continue
        pw, ph = pil.size
        mode = pil.mode
        rgb = pil.convert("RGB") if mode not in ("RGB",) else pil
        changed = False
        for (rx0, ry0, rx1, ry1) in rects:
            # page rect -> image pixel rect (placement axis-aligned approx)
            ix0 = int(max(0, min(1, (min(rx0, rx1) - prect[0]) / max(1e-6, prect[2] - prect[0]))) * pw)
            ix1 = int(max(0, min(1, (max(rx0, rx1) - prect[0]) / max(1e-6, prect[2] - prect[0]))) * pw)
            # page y-up -> image row-down
            iy0 = int(max(0, min(1, (prect[3] - max(ry0, ry1)) / max(1e-6, prect[3] - prect[1]))) * ph)
            iy1 = int(max(0, min(1, (prect[3] - min(ry0, ry1)) / max(1e-6, prect[3] - prect[1]))) * ph)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            from PIL import ImageDraw
            dr = ImageDraw.Draw(rgb)
            dr.rectangle([ix0, iy0, max(ix0 + 1, ix1 - 1), max(iy0 + 1, iy1 - 1)],
                         fill=tuple(fill))
            changed = True
        if not changed:
            continue
        # re-encode in place: pypdf EncodedStreamObject.set_data takes RAW samples
        # and re-applies FlateDecode itself (so /Filter must be plain /FlateDecode)
        raw = rgb.tobytes()
        xobj[NameObject("/Filter")] = NameObject("/FlateDecode")
        xobj.set_data(raw)  # raises unless filter is (now) plain FlateDecode
        xobj[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
        xobj[NameObject("/BitsPerComponent")] = NumberObject(8)
        if name not in edited:
            edited.append(name)
    if not edited:
        return {"edited": [], "rects": len(rects)}
    out = io.BytesIO()
    w.write(out)
    return {"bytes": out.getvalue(), "edited": edited, "rects": len(rects)}
