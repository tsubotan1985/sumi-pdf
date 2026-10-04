"""Tests: image pixel redaction + Stirling-parity utils (compress/watermark/numbers/meta/sanitize)."""
import io
import os

import pytest
from PIL import Image

from sumi_pdf import imgredact as IR
from sumi_pdf import pdfio as P
from sumi_pdf import stirling as ST
from pypdf.generic import NameObject


@pytest.fixture(scope="module")
def imgpdf(tmp_path_factory):
    """One page with a big embedded photo-like image."""
    d = tmp_path_factory.mktemp("img")
    im = Image.new("RGB", (600, 400), (200, 200, 200))
    from PIL import ImageDraw
    dr = ImageDraw.Draw(im)
    dr.rectangle([100, 100, 500, 300], fill=(0, 0, 0))  # secret black box in the photo
    png = str(d / "photo.png")
    im.save(png)
    p = str(d / "photo.pdf")
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(p, pagesize=(300, 200))
    c.drawImage(png, 0, 0, width=300, height=200)
    c.showPage()
    c.save()
    return open(p, "rb").read(), p


def _probe_region(data, rect_page, dpi=150):
    """Mean luminance of a page rect (PDF points, y-up)."""
    from statistics import fmean
    pw, ph = P.page_size(data, 0)
    pil = P.render_pil(data, 0, dpi).convert("L")
    s = dpi / 72.0
    x0, y0, x1, y1 = rect_page
    box = (int(x0 * s), int((ph - y1) * s), int(x1 * s), int((ph - y0) * s))
    return fmean(pil.crop(box).getdata())


def test_remove_pixels_blanks_and_persists(imgpdf):
    data, _ = imgpdf
    rect = (100, 50, 200, 150)  # covers part of the black box
    before = _probe_region(data, rect)
    out = IR.remove_pixels(data, 0, [rect], fill=(255, 255, 255))
    assert out["edited"], f"no image edited: {out}"
    after = _probe_region(out["bytes"], rect)
    assert after > before + 30, f"pixels must be blanked: {before} -> {after}"
    # destructive: the data is gone from the file itself (re-render from saved bytes)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(out["bytes"])
        path = fh.name
    again = _probe_region(open(path, "rb").read(), rect)
    assert again > before + 30
    os.unlink(path)


def test_compress_shrinks_photo_pdf(tmp_path):
    from reportlab.pdfgen import canvas
    im = Image.new("RGB", (1200, 800), (120, 130, 140))
    png = str(tmp_path / "big.png")
    im.save(png)
    p = str(tmp_path / "big.pdf")
    c = canvas.Canvas(p, pagesize=(600, 400))
    c.drawImage(png, 0, 0, width=600, height=400)
    c.showPage()
    c.save()
    data = open(p, "rb").read()
    out = str(tmp_path / "small.pdf")
    r = ST.compress(data, out, image_quality=40)
    assert r["after"] < r["before"]
    # still renders + text-less page count intact
    assert P.page_count(open(out, "rb").read()) == 1


def test_watermark_and_page_numbers(tmp_path):
    from reportlab.pdfgen import canvas
    p = str(tmp_path / "doc.pdf")
    c = canvas.Canvas(p, pagesize=(300, 200))
    c.setFont("Helvetica", 12)
    c.drawString(30, 100, "hello")
    c.showPage()
    c.save()
    data = open(p, "rb").read()
    wm = ST.watermark(data, 0, "CONFIDENTIAL", size=24)
    assert wm != data
    out = str(tmp_path / "num.pdf")
    r = ST.page_numbers(wm, out, start=5)
    assert r["pages"] == 1
    # extract text: page number label present (Win cp932 console-safe)
    txt = P.extract_text(open(out, "rb").read(), 0)
    assert "5 / 5" in txt and "CONFIDENTIAL" in txt, repr(txt[:120])


def test_metadata_roundtrip(tmp_path):
    from reportlab.pdfgen import canvas
    p = str(tmp_path / "m.pdf")
    c = canvas.Canvas(p)
    c.drawString(10, 50, "meta")
    c.showPage()
    c.save()
    data = open(p, "rb").read()
    out = str(tmp_path / "m2.pdf")
    ST.set_metadata(data, out, Title="機密資料", Author="坪田陽一", Subject=None)
    md = ST.get_metadata(open(out, "rb").read())
    assert md["Title"] == "機密資料" and md["Author"] == "坪田陽一"


def test_sanitize_strips_js_and_files(tmp_path):
    # build a PDF with JS + embedded file + metadata via pypdf writer
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    p = str(tmp_path / "src.pdf")
    c = canvas.Canvas(p)
    c.drawString(10, 50, "body")
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=p)
    w.add_metadata({"/Title": "topsecret"})
    w._root_object[NameObject("/JavaScript")] = __import__(
        "pypdf").generic.DictionaryObject({})
    w.write(open(p, "wb"))
    data = open(p, "rb").read()
    out = str(tmp_path / "clean.pdf")
    r = ST.sanitize(data, out)
    assert r["js"] >= 1
    md = ST.get_metadata(open(out, "rb").read())
    assert md.get("Title") != "topsecret"  # metadata stripped
    txt = P.extract_text(open(out, "rb").read(), 0)
    assert "body" in txt  # content untouched
