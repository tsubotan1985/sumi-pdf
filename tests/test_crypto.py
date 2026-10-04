import os

import pytest

from sumi_pdf import crypto as C
from sumi_pdf import fonts as F
from sumi_pdf import pdfio as P


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    d = tmp_path_factory.mktemp("crypto")
    pdfmetrics.registerFont(TTFont("IPA", F.bundled_path("ipaexg.ttf")))
    p = str(d / "plain.pdf")
    c = canvas.Canvas(p, pagesize=(595, 842))
    c.setFont("IPA", 14)
    c.drawString(80, 760, "機密文書 暗号化テスト")
    c.showPage()
    c.save()
    return p


def test_encrypt_sets_password_and_restricts_copy(plain):
    out = plain.replace(".pdf", "_enc.pdf")
    r = C.encrypt_pdf(plain, out, user_password="abc", owner_password="root",
                      print_=True, copy=False, modify=False)
    assert r["encrypted"] and os.path.isfile(out)
    assert C.is_encrypted(out)
    info = C.inspect(out, password="abc")
    assert info["encrypted"] is True
    assert info["flags"]["copy"] is False
    assert info["flags"]["print"] is True


def test_decrypt_roundtrip(plain):
    enc = plain.replace(".pdf", "_enc2.pdf")
    C.encrypt_pdf(plain, enc, user_password="abc")
    out = plain.replace(".pdf", "_dec.pdf")
    C.decrypt_pdf(enc, out, password="abc")
    assert not C.is_encrypted(out)
    assert "暗号化テスト" in P.extract_text(open(out, "rb").read(), 0)


def test_decrypt_wrong_password_raises(plain):
    enc = plain.replace(".pdf", "_enc3.pdf")
    C.encrypt_pdf(plain, enc, user_password="abc")
    with pytest.raises(ValueError, match="wrong password"):
        C.decrypt_pdf(enc, str(enc) + ".dec.pdf", password="zzz")


def test_owner_pw_only_opens_without_prompt(plain):
    enc = plain.replace(".pdf", "_own.pdf")
    C.encrypt_pdf(plain, enc, owner_password="root", user_password="",
                  print_=True, copy=False)
    info = C.inspect(enc, password="")  # empty user pw opens
    assert info["flags"]["copy"] is False


def test_encrypt_from_bytes():
    from reportlab.pdfgen import canvas
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 100))
    c.drawString(10, 50, "bytes-enc")
    c.save()
    out_bytes = buf.getvalue()
    out = out_bytes.replace(b"%%EOF", b"%%EOF")  # keep type
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(out_bytes)
        src = fh.name
    enc = src + ".enc.pdf"
    C.encrypt_pdf(out_bytes, enc, user_password="k")
    assert C.is_encrypted(enc)
    os.unlink(src)
    os.unlink(enc)
