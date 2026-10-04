import os

import pymupdf as fitz
import pytest

from sumi_pdf import crypto as C
from sumi_pdf import fonts as F


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


def test_encrypt_sets_password_and_denies_copy(plain):
    out = plain.replace(".pdf", "_enc.pdf")
    r = C.encrypt_pdf(plain, out, user_pw="abc", owner_pw="root",
                      allow={"print": True, "copy": False, "modify": False})
    assert r["encrypted"] and os.path.isfile(out)
    d = fitz.open(out)
    assert d.needs_pass, "user password must be required"
    d.close()
    assert C.is_encrypted(out)


def test_decrypt_roundtrip(plain):
    enc = plain.replace(".pdf", "_enc2.pdf")
    C.encrypt_pdf(plain, enc, user_pw="abc")
    out = plain.replace(".pdf", "_dec.pdf")
    C.decrypt_pdf(enc, out, password="abc")
    assert not C.is_encrypted(out)
    d = fitz.open(out)
    assert "暗号化テスト" in d[0].get_text()
    d.close()


def test_decrypt_wrong_password_raises(plain):
    enc = plain.replace(".pdf", "_enc3.pdf")
    C.encrypt_pdf(plain, enc, user_pw="abc")
    with pytest.raises(RuntimeError, match="wrong password"):
        C.decrypt_pdf(enc, str(enc) + ".dec.pdf", password="zzz")


def test_owner_pw_only_opens_without_prompt_but_restricts(plain):
    enc = plain.replace(".pdf", "_own.pdf")
    C.encrypt_pdf(plain, enc, owner_pw="root", user_pw="", allow={"print": True, "copy": False})
    d = fitz.open(enc)  # opens without user pw
    assert not d.needs_pass or d.authenticate("")
    d.close()
    info = C.pdf_permissions(enc)
    assert info["flags"]["print"] is True
    assert info["flags"]["copy"] is False
