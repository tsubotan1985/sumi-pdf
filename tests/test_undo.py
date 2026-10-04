"""Undo/redo history stack: snapshots on edit, caps, busy guard, dirty linkage."""
import io

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sumi_pdf import server as SV

DEFAULT_STEPS = SV.UNDO_MAX_STEPS
DEFAULT_BYTES = SV.UNDO_MAX_BYTES


def _pdf_bytes(pages: int = 2, label: str = "undo sample") -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    for i in range(pages):
        c.drawString(30, 330, f"{label} {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _make_pdf(path: str, pages: int = 2, label: str = "undo sample") -> str:
    with open(path, "wb") as fh:
        fh.write(_pdf_bytes(pages, label))
    return path


@pytest.fixture()
def reset():
    """Restore S, both stacks and the limits so other test files are untouched."""
    yield
    SV.S.update({"src": None, "work": None, "path": None, "dirty": False,
                 "stat": None, "n": 0, "pw": None, "busy": False, "desktop": False})
    SV._UNDO.clear()
    SV._REDO.clear()
    SV.UNDO_MAX_STEPS = DEFAULT_STEPS
    SV.UNDO_MAX_BYTES = DEFAULT_BYTES


def _open(tmp_path, pages: int = 2, label: str = "undo sample"):
    return SV.open_pdf(SV.OpenReq(
        path=_make_pdf(str(tmp_path / "doc.pdf"), pages, label)))


# ------------------------------------------------------- push + undo restores

def test_redact_pushes_history_and_undo_restores(tmp_path, reset):
    _open(tmp_path)
    original = SV.S["work"]
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    edited = SV.S["work"]
    assert SV.S["dirty"] is True and edited != original

    r = SV.do_undo(SV.UndoReq(page=0))

    assert r["undone"] is True
    assert r["pages"] == 2
    assert r["page"] == 0
    assert len(r["size"]) == 2                      # undo後 pages/size を返す
    assert SV.S["work"] == original                 # work bytes restored
    assert SV.S["dirty"] is False                   # pre-edit dirty restored
    assert SV.S["n"] == 2


def test_redo_reapplies_the_undone_edit(tmp_path, reset):
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    edited = SV.S["work"]
    SV.do_undo(SV.UndoReq(page=0))

    r = SV.do_redo(SV.UndoReq(page=0))

    assert r["undone"] is True                      # redo is the same response shape
    assert SV.S["work"] == edited
    assert SV.S["dirty"] is True


def test_undo_response_clamps_page_to_restored_count(tmp_path, reset):
    _open(tmp_path, pages=3)
    SV.pages_delete(SV.PagesReq(pnos=[2]))
    assert SV.S["n"] == 2

    r = SV.do_undo(SV.UndoReq(page=9))              # request page beyond restored doc

    assert r["pages"] == 3
    assert r["page"] == 2                           # clamped to last page
    assert SV.S["n"] == 3


def test_replace_watermark_rotate_each_push_history(tmp_path, reset):
    _open(tmp_path, pages=1, label="Hello World")
    pristine = SV.S["work"]                          # == SV.S["src"] at open

    SV.do_replace(SV.ReplaceReq(page=0, find="World", replace="Wurld"))
    replaced = SV.S["work"]
    assert replaced != pristine
    assert SV.do_undo(SV.UndoReq())["undone"] is True
    assert SV.S["work"] == pristine                  # replace undone

    assert SV.do_redo(SV.UndoReq())["undone"] is True
    assert SV.S["work"] == replaced

    SV.do_watermark(SV.WatermarkReq(page=0, text="TOP SECRET"))
    watermarked = SV.S["work"]
    assert SV.do_undo(SV.UndoReq())["undone"] is True
    assert SV.S["work"] == replaced                  # watermark undone, replace kept

    assert SV.do_redo(SV.UndoReq())["undone"] is True
    SV.pages_rotate(SV.RotateReq(page=0, deg=90))
    assert SV.do_undo(SV.UndoReq())["undone"] is True
    assert SV.S["work"] == watermarked               # rotate undone


def test_insert_pushes_history_and_undo_restores_count(tmp_path, reset):
    _open(tmp_path, pages=2)
    extra = _make_pdf(str(tmp_path / "extra.pdf"), 1, "extra")
    SV.pages_insert(SV.InsertReq(path=extra, at=None))
    assert SV.S["n"] == 3

    r = SV.do_undo(SV.UndoReq(page=2))

    assert r["pages"] == 2 and SV.S["n"] == 2
    assert SV.S["dirty"] is False


def test_img_redact_without_edits_pushes_no_history(tmp_path, reset):
    _open(tmp_path)                                 # text-only sample: no images
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    n_undo = len(SV._UNDO)

    SV.img_redact(SV.ImgRedactReq(page=0, rects=[[10, 10, 50, 50]]))

    assert len(SV._UNDO) == n_undo                  # no-op edit must not pollute history


# --------------------------------------------------------------- empty / 400

def test_undo_with_empty_stack_is_400_detail(tmp_path, reset):
    _open(tmp_path)
    with pytest.raises(HTTPException) as exc:
        SV.do_undo(SV.UndoReq())
    assert exc.value.status_code == 400
    assert exc.value.detail                         # {detail} shape for the UI

    with pytest.raises(HTTPException) as exc:
        SV.do_redo(SV.UndoReq())
    assert exc.value.status_code == 400


def test_undo_without_document_is_400(tmp_path, reset):
    with pytest.raises(HTTPException) as exc:
        SV.do_undo(SV.UndoReq())
    assert exc.value.status_code == 400


def test_redo_cleared_by_new_edit(tmp_path, reset):
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    SV.do_undo(SV.UndoReq())
    SV.do_redact(SV.RedactReq(page=0, rects=[[50, 50, 80, 80]]))   # fresh edit

    with pytest.raises(HTTPException) as exc:
        SV.do_redo(SV.UndoReq())
    assert exc.value.status_code == 400             # redo branch invalidated


def test_undo_redo_over_http_shape(tmp_path, reset):
    _open(tmp_path)
    client = TestClient(SV.app)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))

    resp = client.post("/api/undo", json={"page": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["undone"] is True and body["pages"] == 2 and "size" in body

    empty = client.post("/api/undo", json={})
    assert empty.status_code == 400 and "detail" in empty.json()

    resp = client.post("/api/redo", json={"page": 0})
    assert resp.status_code == 200 and resp.json()["undone"] is True
    assert client.post("/api/redo", json={}).status_code == 400


# ------------------------------------------------------------------- caps

def test_step_limit_drops_oldest(tmp_path, reset, monkeypatch):
    monkeypatch.setattr(SV, "UNDO_MAX_STEPS", 2)
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    after_first = SV.S["work"]
    SV.do_redact(SV.RedactReq(page=0, rects=[[110, 110, 150, 150]]))
    SV.do_redact(SV.RedactReq(page=0, rects=[[200, 200, 250, 250]]))
    assert len(SV._UNDO) == 2                       # capped

    SV.do_undo(SV.UndoReq())                        # -> after 2nd edit
    SV.do_undo(SV.UndoReq())                        # -> after 1st edit
    assert SV.S["work"] == after_first

    with pytest.raises(HTTPException) as exc:       # pristine state was evicted
        SV.do_undo(SV.UndoReq())
    assert exc.value.status_code == 400


def test_byte_limit_drops_oldest(tmp_path, reset, monkeypatch):
    _open(tmp_path)
    budget = len(SV.S["work"])                      # room for exactly one snapshot
    monkeypatch.setattr(SV, "UNDO_MAX_BYTES", budget)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    after_first = SV.S["work"]
    SV.do_redact(SV.RedactReq(page=0, rects=[[110, 110, 150, 150]]))
    assert len(SV._UNDO) == 1                       # oldest evicted by bytes

    SV.do_undo(SV.UndoReq())
    assert SV.S["work"] == after_first
    with pytest.raises(HTTPException):
        SV.do_undo(SV.UndoReq())


def test_open_upload_reload_clear_history(tmp_path, reset):
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    assert SV._UNDO

    _open(tmp_path)                                 # same path, fresh open
    assert not SV._UNDO and not SV._REDO
    with pytest.raises(HTTPException):
        SV.do_undo(SV.UndoReq())

    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    SV.do_save(SV.SaveReq())                        # dirty cleared...
    SV.reload_doc(SV.ReloadReq(page=0))             # ...then reload wipes stacks
    assert not SV._UNDO and not SV._REDO

    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    assert SV._UNDO
    client = TestClient(SV.app)
    resp = client.post("/api/upload?filename=u.pdf",
                       content=_pdf_bytes(), headers={"Content-Type": "application/pdf"})
    assert resp.status_code == 200
    assert not SV._UNDO and not SV._REDO            # upload is a new document


# -------------------------------------------------------------- dirty linkage

def test_dirty_follows_snapshots_across_save(tmp_path, reset):
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    SV.do_save(SV.SaveReq())
    assert SV.S["dirty"] is False

    SV.do_undo(SV.UndoReq())
    assert SV.S["dirty"] is False                   # snapshot at edit time was clean
    SV.do_redo(SV.UndoReq())
    assert SV.S["dirty"] is False                   # snapshot at undo time was clean

    SV.do_undo(SV.UndoReq())
    SV.do_redact(SV.RedactReq(page=0, rects=[[50, 50, 80, 80]]))
    assert SV.S["dirty"] is True                    # new edit marks dirty again


# --------------------------------------------------------------- busy handling

def test_busy_blocks_undo_redo_and_reload(tmp_path, reset):
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    SV.S["busy"] = True
    try:
        for fn in (SV.do_undo, SV.do_redo):
            with pytest.raises(HTTPException) as exc:
                fn(SV.UndoReq())
            assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            SV.reload_doc(SV.ReloadReq(page=0))
        assert exc.value.status_code == 409
    finally:
        SV.S["busy"] = False


def test_busy_flag_set_only_during_restore(tmp_path, reset, monkeypatch):
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    seen = {}
    real = SV._apply_snapshot

    def spy(sn):
        seen["busy"] = SV.S["busy"]
        real(sn)

    monkeypatch.setattr(SV, "_apply_snapshot", spy)
    SV.do_undo(SV.UndoReq())
    assert seen["busy"] is True                     # busy during the swap...
    assert SV.S["busy"] is False                    # ...and cleared afterwards


def test_reload_allowed_again_after_busy_clears(tmp_path, reset):
    _open(tmp_path)
    SV.do_redact(SV.RedactReq(page=0, rects=[[10, 10, 100, 100]]))
    SV.do_save(SV.SaveReq())
    SV.S["busy"] = True
    with pytest.raises(HTTPException):
        SV.reload_doc(SV.ReloadReq(page=0))
    SV.S["busy"] = False
    assert SV.reload_doc(SV.ReloadReq(page=0))["pages"] == 2
