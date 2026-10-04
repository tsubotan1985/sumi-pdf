import os

import pymupdf as fitz
import pytest

from sumi_pdf import fonts as F
from sumi_pdf import textlayer
from sumi_pdf.ocr import detect_engines, parse_tsv

ENG = detect_engines()

SAMPLE_TSV = ("level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext\n"
              "5\t1\t0\t0\t0\t0\t100\t200\t300\t40\t92.0\t070-1316\n"
              "5\t1\t0\t0\t1\t1\t100\t260\t120\t40\t88.5\t8712\n")


def test_parse_tsv():
    text, words = parse_tsv(SAMPLE_TSV, dpi=300)
    assert "070-1316" in text and "8712" in text
    assert len(words) == 2
    w0 = words[0]
    assert abs(w0["bbox"][0] - 100 * 72 / 300) < 0.1
    assert w0["conf"] == 92.0
    assert w0["bbox"][2] > w0["bbox"][0]


def test_detect_engines_shape():
    assert set(ENG) >= {"tesseract", "tessdata", "ndlocr_src"}


def test_no_engine_raises_clean(tmp_path):
    from sumi_pdf.ocr import ocr_page
    if ENG["tesseract"]:
        pytest.skip("tesseract installed; error path not reachable")
    p = str(tmp_path / "x.pdf")
    d = fitz.open(); d.new_page(); d.save(p); d.close()
    with pytest.raises(RuntimeError, match="no OCR engine"):
        ocr_page(p, 0)


@pytest.mark.skipif(not ENG["tesseract"], reason="tesseract not installed")
def test_ocr_live_and_searchable_layer(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader

    ttf = F.default_font("gothic")
    im = Image.new("RGB", (1200, 400), "white")
    dr = ImageDraw.Draw(im)
    f44 = ImageFont.truetype(ttf, 56)
    dr.text((60, 80), "御見積書", font=f44, fill="black")
    dr.text((60, 220), "TEL 070-1316-8712", font=f44, fill="black")
    png = str(tmp_path / "scan.png")
    im.save(png)
    doc = fitz.open()
    pg = doc.new_page(width=595, height=400)
    pg.insert_image(fitz.Rect(0, 0, 595, 400), filename=png)
    p = str(tmp_path / "scan.pdf")
    doc.save(p)
    doc.close()

    from sumi_pdf.ocr import ocr_page
    r = ocr_page(p, 0, lang="auto")
    assert r["words"], "OCR must find words"
    joined = r["text"].replace(" ", "").replace("\n", "")
    # tessdata_fast may misread single digits; assert on robust tokens
    assert ("見積" in joined or "TEL" in joined or "070" in joined), r["text"]

    # invisible layer -> searchable
    doc = fitz.open(p)
    n = textlayer.add_layer_doc(doc, 0, r["words"])
    assert n > 0
    out = str(tmp_path / "layer.pdf")
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    d2 = fitz.open(out)
    txt = d2[0].get_text().replace("\xa0", " ")
    assert len(txt) > 10 and ("070" in txt or "TEL" in txt or "見積" in txt), txt
    # invisible: render pixel check not needed; render_mode 3 by construction
    d2.close()
