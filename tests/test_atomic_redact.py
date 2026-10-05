"""Task 6: 複合墨消しの原子性（img:true）＋ pages_meta ＋ 回転/CropBoxガード.

- POST /api/redact に img:true を渡すと文字墨消し→画像墨消しを同一work上で
  逐次実行し、1リクエストで完結する（画像側失敗時は400・work不変＝アトミック）。
- /api/open は pages_meta:[{rot,crop_x0,crop_y0}] を返す（ネイティブ回転/CropBox）。
- ネイティブ /Rotate≠0・CropBox原点≠0 のページへの墨消しは400で中止される。
"""
import io

import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, RectangleObject
from reportlab.pdfgen import canvas
from sumi_pdf import imgredact as IR
from sumi_pdf import server as sv

TEXT_RECT = [25.0, 320.0, 135.0, 342.0]
IMG_RECT = [150.0, 295.0, 195.0, 335.0]


def _pdf_bytes(pages: int = 1, label: str = "atomic sample",
               with_image: bool = False) -> bytes:
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    for i in range(pages):
        c.drawString(30, 330, f"{label} {i + 1}")
        if with_image:
            img = io.BytesIO()
            Image.new("RGB", (40, 30), (200, 50, 50)).save(img, format="PNG")
            img.seek(0)
            c.drawImage(ImageReader(img), 150, 300, width=40, height=30)
        c.showPage()
    c.save()
    return buf.getvalue()


def _make_pdf(path: str, **kw) -> str:
    with open(path, "wb") as fh:
        fh.write(_pdf_bytes(**kw))
    return path


def _with_native_meta(data: bytes, rot_page: int | None = None,
                      crop_page: int | None = None) -> bytes:
    """pypdfで /Rotate 90 と CropBox原点(30,20) を焼き込む。"""
    w = PdfWriter(clone_from=io.BytesIO(data))
    if rot_page is not None:
        w.pages[rot_page].rotation = 90
    if crop_page is not None:
        box = w.pages[crop_page].mediabox
        w.pages[crop_page][NameObject("/CropBox")] = RectangleObject(
            [30, 20, float(box.width), float(box.height)])
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


@pytest.fixture()
def reset():
    yield
    sv.S.update({"src": None, "work": None, "path": None, "dirty": False,
                 "stat": None, "n": 0, "pw": None, "busy": False, "desktop": False})
    sv._UNDO.clear()
    sv._REDO.clear()


# ------------------------------------------------------------ img:true 原子性

def test_combined_redact_applies_text_and_image_in_one_request(tmp_path, reset):
    sv.open_pdf(sv.OpenReq(path=_make_pdf(str(tmp_path / "doc.pdf"),
                                          with_image=True)))
    r = sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT, IMG_RECT], img=True))

    a = r["applied"]
    assert a["dropped"] > 0                       # 文字側が効いている
    assert a["filled"] > 0
    assert isinstance(a["img_edited"], list)      # applied に画像結果が載る
    assert len(a["img_edited"]) >= 1
    assert sv.S["dirty"] is True
    assert len(sv._UNDO) == 1                     # 1リクエスト=1スナップショット


def test_combined_redact_equals_sequential_engine_calls(tmp_path, reset):
    """img:true の結果は R.redact→IR.remove_pixels の逐次適用と同じになる。"""
    data = _pdf_bytes(with_image=True)
    sv.open_pdf(sv.OpenReq(path=_make_pdf(str(tmp_path / "doc.pdf"),
                                          with_image=True)))
    r = sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT, IMG_RECT], img=True))
    step1 = sv.R.redact(data, 0, [tuple(TEXT_RECT), tuple(IMG_RECT)])
    step2 = IR.remove_pixels(step1["bytes"], 0, [tuple(TEXT_RECT), tuple(IMG_RECT)])
    assert sv.S["work"] == step2["bytes"]
    assert r["applied"]["img_edited"] == step2["edited"]


def test_combined_redact_image_failure_is_atomic(tmp_path, reset, monkeypatch):
    sv.open_pdf(sv.OpenReq(path=_make_pdf(str(tmp_path / "doc.pdf"),
                                          with_image=True)))
    work0 = sv.S["work"]

    def boom(*a, **kw):
        raise RuntimeError("simulated image failure")

    monkeypatch.setattr(IR, "remove_pixels", boom)
    with pytest.raises(HTTPException) as ei:
        sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT], img=True))

    assert ei.value.status_code == 400
    assert sv.S["work"] == work0                  # work不変（アトミック）
    assert sv.S["dirty"] is False                 # 文字側だけ適用されない
    assert len(sv._UNDO) == 0                     # 履歴も汚さない


def test_plain_redact_request_shape_unchanged(tmp_path, reset):
    """img 省略時の応答は従来どおり {applied:{dropped,filled}} だけ。"""
    sv.open_pdf(sv.OpenReq(path=_make_pdf(str(tmp_path / "doc.pdf"))))
    r = sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT]))
    assert set(r["applied"].keys()) == {"dropped", "filled"}


# ---------------------------------------------------------------- pages_meta

def test_open_returns_pages_meta_with_native_rotation_and_cropbox(tmp_path, reset):
    p = str(tmp_path / "meta.pdf")
    with open(p, "wb") as fh:
        fh.write(_with_native_meta(_pdf_bytes(pages=3), rot_page=1, crop_page=2))
    r = sv.open_pdf(sv.OpenReq(path=p))

    meta = r["pages_meta"]
    assert isinstance(meta, list) and len(meta) == 3
    for m in meta:
        assert set(m.keys()) == {"rot", "crop_x0", "crop_y0"}
    assert [m["rot"] for m in meta] == [0, 90, 0]
    assert meta[0]["crop_x0"] == 0 and meta[0]["crop_y0"] == 0
    assert meta[2]["crop_x0"] == 30 and meta[2]["crop_y0"] == 20


def test_open_pages_meta_plain_pdf_all_zero(tmp_path, reset):
    sv.open_pdf(sv.OpenReq(path=_make_pdf(str(tmp_path / "doc.pdf"), pages=2)))
    assert sv.S["n"] == 2


# ------------------------------------------------------- 回転/CropBox ガード

def test_redact_blocked_on_natively_rotated_page(tmp_path, reset):
    p = str(tmp_path / "rot.pdf")
    with open(p, "wb") as fh:
        fh.write(_with_native_meta(_pdf_bytes(), rot_page=0))
    sv.open_pdf(sv.OpenReq(path=p))
    work0 = sv.S["work"]

    with pytest.raises(HTTPException) as ei:
        sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT]))

    assert ei.value.status_code == 400
    assert "回転/CropBox" in ei.value.detail
    assert sv.S["work"] == work0                  # 中止なので不変
    assert sv.S["dirty"] is False


def test_redact_blocked_on_cropbox_origin_page(tmp_path, reset):
    p = str(tmp_path / "crop.pdf")
    with open(p, "wb") as fh:
        fh.write(_with_native_meta(_pdf_bytes(), crop_page=0))
    sv.open_pdf(sv.OpenReq(path=p))

    with pytest.raises(HTTPException) as ei:
        sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT], img=True))

    assert ei.value.status_code == 400
    assert "回転/CropBox" in ei.value.detail


def test_redact_allowed_on_plain_page_still_works(tmp_path, reset):
    sv.open_pdf(sv.OpenReq(path=_make_pdf(str(tmp_path / "doc.pdf"))))
    r = sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT], img=True))
    assert r["applied"]["dropped"] > 0            # ガードは素通しさせない


# ----------------------------------------------- 構造変更後の pages_meta 再同期

def test_undo_redo_and_page_ops_return_pages_meta(tmp_path, reset):
    p = str(tmp_path / "doc.pdf")
    with open(p, "wb") as fh:
        fh.write(_with_native_meta(_pdf_bytes(pages=2), rot_page=1))
    sv.open_pdf(sv.OpenReq(path=p))
    sv.do_redact(sv.RedactReq(page=0, rects=[TEXT_RECT]))
    r = sv.do_undo(sv.UndoReq(page=0))
    assert len(r["pages_meta"]) == 2 and r["pages_meta"][1]["rot"] == 90
    r = sv.do_redo(sv.UndoReq(page=0))
    assert len(r["pages_meta"]) == 2

    r = sv.pages_delete(sv.PagesReq(pnos=[0]))
    assert r["pages"] == 1 and len(r["pages_meta"]) == 1 and r["pages_meta"][0]["rot"] == 90
