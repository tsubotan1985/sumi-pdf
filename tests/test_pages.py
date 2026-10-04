"""Page operations via pypdf: rotate / delete / extract / merge / split."""
import io
import os

from pypdf import PdfReader

from sumi_pdf import pages as PG


def _pdf(n=3):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 200))
    for i in range(n):
        c.setFont("Helvetica", 14)
        c.drawString(20, 100, f"p{i+1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_rotate_marks_and_renders():
    data = _pdf(1)
    out = PG.rotate(data, 0, 90)
    r = PdfReader(io.BytesIO(out))
    assert r.pages[0].get("/Rotate") == 90


def test_delete_and_extract():
    data = _pdf(3)
    out = PG.delete_pages(data, [1])
    assert len(PdfReader(io.BytesIO(out)).pages) == 2
    ex = PG.extract_pages(data, [2])
    r = PdfReader(io.BytesIO(ex))
    assert len(r.pages) == 1


def test_split_one_file_per_page(tmp_path):
    data = _pdf(3)
    files = PG.split(data, str(tmp_path), "doc")
    assert len(files) == 3 and all(os.path.isfile(f) for f in files)


def test_merge_pdfs(tmp_path):
    a = os.path.join(tmp_path, "a.pdf")
    b = os.path.join(tmp_path, "b.pdf")
    open(a, "wb").write(_pdf(2))
    open(b, "wb").write(_pdf(1))
    out = os.path.join(tmp_path, "m.pdf")
    r = PG.merge_pdfs([a, b], out)
    assert r["pages"] == 3
