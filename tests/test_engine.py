"""MIT engine tests: true redaction, replacement, rendering (pypdfium2/pypdf/reportlab)."""
import io
import os

import pytest

from sumi_pdf import edit as E
from sumi_pdf import fonts as F
from sumi_pdf import pdfio as P
from sumi_pdf import redact as R
from sumi_pdf import textedit as T

IPA = F.bundled_path("ipaexg.ttf")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("IPA", IPA))
    d = tmp_path_factory.mktemp("smp")
    p = str(d / "sample.pdf")
    c = canvas.Canvas(p, pagesize=(595, 842))
    c.setFont("IPA", 16)
    c.drawString(80, 760, "御見積書　株式会社サンプル　担当：坪田陽一")
    c.setFont("IPA", 12)
    c.drawString(80, 720, "連絡先 070-1316-8712")
    c.showPage()
    c.save()
    # page-2 style doc: image (scan-like)
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", (900, 200), "white")
    dr = ImageDraw.Draw(im)
    dr.text((20, 60), "機密：坪田陽一 070-1316-8712",
            font=ImageFont.truetype(IPA, 44), fill="black")
    png = str(d / "scan.png")
    im.save(png)
    ip = str(d / "img.pdf")
    c = canvas.Canvas(ip, pagesize=(595, 200))
    c.drawImage(png, 20, 40, width=555, height=130)
    c.showPage()
    c.save()
    return open(p, "rb").read(), open(ip, "rb").read(), p, ip


def _text(data, pno=0):
    return P.extract_text(data, pno).replace("\n", "")


def test_search_finds_occurrence(sample):
    data = sample[0]
    occ = P.search(data, 0, "070-1316-8712")
    assert len(occ) == 1
    x0, y0, x1, y1 = occ[0].rect
    assert x1 > x0 and y1 > y0 and 700 < occ[0].origin[1] < 730


def test_redact_removes_text(sample):
    data = sample[0]
    occ = P.search(data, 0, "070-1316-8712")
    out = R.redact(data, 0, [occ[0].rect])
    txt = _text(out["bytes"])
    assert "070-1316-8712" not in txt
    assert "株式会社サンプル" in txt  # 周囲は無傷
    assert out["dropped"] >= 1


def test_redact_covers_image_area(sample):
    _, ip, _, _ = sample
    from statistics import fmean

    rect = (60, 60, 320, 110)
    before = fmean(P.render_pil(ip, 0, 150).convert("L").crop(
        (int(rect[0] * 150 / 72), int((200 - rect[3]) * 150 / 72),
         int(rect[2] * 150 / 72), int((200 - rect[1]) * 150 / 72))).getdata())
    out = R.redact(ip, 0, [rect], match_bg=False, fill=(0, 0, 0))
    after = fmean(P.render_pil(out["bytes"], 0, 150).convert("L").crop(
        (int(rect[0] * 150 / 72), int((200 - rect[3]) * 150 / 72),
         int(rect[2] * 150 / 72), int((200 - rect[1]) * 150 / 72))).getdata())
    assert after < before - 40  # 黒で塗りつぶされている


def test_replace_same_baseline(sample):
    data, _, _, _ = sample
    occ = P.search(data, 0, "坪田陽一")
    assert occ
    y0 = occ[0].origin[1]
    r = T.replace(data, 0, "坪田陽一", "坪田 洋一")
    assert r["replaced"] == 1
    out = r["bytes"]
    txt = _text(out).replace("\xa0", " ")
    assert "坪田 洋一" in txt and "坪田陽一" not in txt
    occ2 = P.search(out, 0, "坪田 洋一")
    assert occ2
    assert abs(occ2[0].origin[1] - y0) < 2.0  # ベースライン維持


def test_state_machine_keeps_gfx_state(sample):
    """q/Q balance: removal must not break the graphics stack (render still OK)."""
    data = sample[0]
    occ = P.search(data, 0, "070-1316-8712")
    out, metas = E.remove_text_in_rects(data, 0, [occ[0].rect])
    assert len(metas) >= 1
    P.render_pil(out, 0, 100)  # pdfium can still render (no unbalanced q/Q crash)
    pil = P.render_pil(out, 0, 150)
    assert pil.size[0] > 0


def test_font_alias_resolution():
    r = F.resolve("MS-Mincho")
    assert r["path"], "MS Mincho fallback must resolve (system or bundled)"
    assert F.resolve("AAAAAA+IPAexGothic")["path"]
    assert F.default_font("mincho").endswith("ipaexm.ttf")
