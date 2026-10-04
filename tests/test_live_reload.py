"""SumatraPDF-style live reload: no-lock open + external-update detection."""
import os
import time

import pymupdf as fitz
import pytest

from sumi_pdf import server as SV


def _make_pdf(path: str, n_pages: int):
    doc = fitz.open()
    for i in range(n_pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"page {i+1}", fontsize=20)
    doc.save(path, garbage=4, deflate=True)
    doc.close()


@pytest.fixture()
def reset_state():
    yield
    SV.S.update({"doc": None, "path": None, "dirty": False, "stat": None})


def _open(path):
    return SV.open_pdf(SV.OpenReq(path=path))


def test_open_does_not_lock_file(tmp_path, reset_state):
    """While SUMIPDF has the doc open, another app can atomically replace the file."""
    p = str(tmp_path / "live.pdf")
    _make_pdf(p, 3)
    r = _open(p)
    assert r["pages"] == 3
    # simulate Word/editor save: atomic replace over the same path
    p2 = str(tmp_path / "new.pdf")
    _make_pdf(p2, 2)
    os.replace(p2, p)  # would fail on Windows if we held a write lock
    fi = SV.file_info()
    assert fi["disk"]["size"] != fi["saved"]["size"], "external change must be detectable"


def test_reload_keeps_page_and_updates_count(tmp_path, reset_state):
    p = str(tmp_path / "live.pdf")
    _make_pdf(p, 3)
    _open(p)
    p2 = str(tmp_path / "new.pdf")
    _make_pdf(p2, 2)
    os.replace(p2, p)
    r = SV.reload_doc(SV.ReloadReq(page=2))
    assert r["pages"] == 2
    assert r["page"] == 1  # clamped to last page
    assert SV.S["doc"][1].get_text().strip() == "page 2"


def test_dirty_guard_blocks_reload(tmp_path, reset_state):
    p = str(tmp_path / "live.pdf")
    _make_pdf(p, 1)
    _open(p)
    SV.do_redact(SV.RedactReq(page=0, rects=[[50, 50, 200, 80]]))
    assert SV.S["dirty"] is True
    p2 = str(tmp_path / "new.pdf")
    _make_pdf(p2, 1)
    os.replace(p2, p)
    with pytest.raises(Exception) as e:
        SV.reload_doc(SV.ReloadReq(page=0))
    assert "409" in str(e.value) or "unsaved" in str(e.value)
    # after save, reload is allowed again
    SV.do_save(SV.SaveReq())
    assert SV.S["dirty"] is False
    r = SV.reload_doc(SV.ReloadReq(page=0))
    assert r["pages"] == 1


def test_reload_missing_file_404(tmp_path, reset_state):
    p = str(tmp_path / "gone.pdf")
    _make_pdf(p, 1)
    _open(p)
    os.remove(p)
    with pytest.raises(Exception) as e:
        SV.reload_doc(SV.ReloadReq(page=0))
    assert "404" in str(e.value) or "gone" in str(e.value)
