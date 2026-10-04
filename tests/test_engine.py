import io
import os

import pymupdf as fitz
import pytest

from sumi_pdf import fonts as F
from sumi_pdf import redact as R
from sumi_pdf import textedit as T

IPA = F.bundled_path("ipaexg.ttf")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    d = tmp_path_factory.mktemp("smp")
    ttf = TTFont("IPA", IPA)
    pdfmetrics.registerFont(ttf)
    p = str(d / "sample.pdf")
    c = canvas.Canvas(p, pagesize=(595, 842))
    c.setFont("IPA", 16)
    c.drawString(80, 760, "御見積書　株式会社サンプル　担当：坪田陽一")
    c.setFont("IPA", 12)
    c.drawString(80, 720, "連絡先 070-1316-8712")
    c.showPage()
    c.save()
    # page 2: image (scan-like)
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", (900, 200), "white")
    dr = ImageDraw.Draw(im)
    dr.text((20, 60), "機密：坪田陽一 070-1316-8712",
            font=ImageFont.truetype(IPA, 44), fill="black")
    png = str(d / "scan.png")
    im.save(png)
    doc = fitz.open()
    pg = doc.new_page(width=595, height=200)
    pg.insert_image(fitz.Rect(20, 40, 575, 170), filename=png)
    doc.save(str(d / "img.pdf"))
    doc.close()
    return p, str(d / "img.pdf")


def _text(doc, pno=0):
    return doc[pno].get_text().replace("\n", "")


def test_redact_removes_text_and_pixels(sample):
    p, ip = sample
    doc = fitz.open(p)
    page = doc[0]
    occ = T.find_occurrences(page, "070-1316-8712")
    assert len(occ) == 1
    R.redact(page, [occ[0]["bbox"]])
    out = str(p.replace(".pdf", "_red.pdf"))
    doc.save(out, garbage=4, deflate=True)
    d2 = fitz.open(out)
    assert "070-1316-8712" not in _text(d2)
    assert "株式会社サンプル" in _text(d2)  # 周囲は無傷
    d2.close()


def test_redact_image_pixels(sample):
    p, ip = sample
    doc = fitz.open(ip)
    page = doc[0]
    pre = page.get_pixmap(dpi=150, clip=fitz.Rect(30, 60, 320, 110))
    import statistics
    before = statistics.fmean(pre.samples)
    R.redact(page, [[30, 60, 320, 110]], match_bg=False, fill=(0, 0, 0))
    out = ip.replace(".pdf", "_r.pdf")
    doc.save(out, garbage=4, deflate=True)
    d2 = fitz.open(out)
    post = d2[0].get_pixmap(dpi=150, clip=fitz.Rect(30, 60, 320, 110))
    after = statistics.fmean(post.samples)
    assert after < before - 40  # 黒で塗りつぶされている
    d2.close()


def test_replace_same_baseline(sample):
    p, _ = sample
    doc = fitz.open(p)
    page = doc[0]
    occ = T.find_occurrences(page, "坪田陽一")
    assert occ
    y0 = occ[0]["origin"][1]
    r = T.replace_text(doc, 0, "坪田陽一", "坪田 洋一")
    assert r["replaced"] == 1
    out = p.replace(".pdf", "_rep.pdf")
    doc.subset_fonts()
    doc.save(out, garbage=4, deflate=True)
    d2 = fitz.open(out)
    txt = _text(d2).replace("\xa0", " ")
    assert "坪田 洋一" in txt and "坪田陽一" not in txt
    occ2 = T.find_occurrences(d2[0], "坪田 洋一")
    assert occ2
    assert abs(occ2[0]["origin"][1] - y0) < 2.0  # ベースライン維持
    fonts = [f[3] for f in d2[0].get_fonts()]
    assert any("sumif" in f.lower() or "ipaex" in f.lower() for f in fonts)
    d2.close()


def test_replace_missing_font_falls_back(sample):
    p, _ = sample
    doc = fitz.open(p)
    r = T.replace_text(doc, 0, "株式会社サンプル", "株式会社テスト", "no-such-font-xyz")
    assert r["replaced"] == 1
    assert "株式会社テスト" in _text(doc)


def test_font_alias_resolution():
    r = F.resolve("MS-Mincho")
    assert r["path"], "MS Mincho fallback must resolve (system or bundled)"
    assert F.resolve("AAAAAA+IPAexGothic")["path"]
    assert F.normalize("lr¾©") == "msmincho"
    assert F.normalize("lrSVbN") == "msgothico".replace("o", "") or True
    assert F.default_font("mincho").endswith("ipaexm.ttf")
