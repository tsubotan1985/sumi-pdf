"""UI support contracts: assets, native dialogs, uploads, and save-as state."""
import io
import os
import sys
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from sumi_pdf import server as sv


def _sample_bytes(pages: int = 1, label: str = "Fictional sample") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    for i in range(pages):
        c.drawString(30, 330, f"{label} {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _client() -> TestClient:
    return TestClient(sv.app)


@pytest.fixture(autouse=True)
def _isolated_state():
    """Snapshot/restore S and the dialog registry so other test files are untouched."""
    old_S = dict(sv.S)
    old_handlers = dict(sv.DIALOG_HANDLERS)
    try:
        yield
    finally:
        sv.S.clear()
        sv.S.update(old_S)
        sv.DIALOG_HANDLERS.clear()
        sv.DIALOG_HANDLERS.update(old_handlers)


# ------------------------------------------------------------- save-as state

def test_save_as_switches_current_document_and_clears_dirty(tmp_path):
    src = tmp_path / "source.pdf"
    original = _sample_bytes()
    src.write_bytes(original)
    sv.open_pdf(sv.OpenReq(path=str(src)))
    sv.S["dirty"] = True
    dest = tmp_path / "saved-copy.pdf"

    result = sv.do_save(sv.SaveReq(path=str(dest)))

    assert result["saved"] == str(dest)
    assert sv.S["path"] == str(dest)          # current document switched
    assert sv.S["dirty"] is False             # dirty cleared
    assert sv.S["stat"]["missing"] is False   # stat synced to the new file
    fi = sv.file_info()
    assert fi["saved"] == fi["disk"]
    assert dest.is_file()
    assert src.read_bytes() == original       # original left untouched


def test_save_as_failure_leaves_state_untouched(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(_sample_bytes())
    sv.open_pdf(sv.OpenReq(path=str(src)))
    sv.S["dirty"] = True
    bad_dest = tmp_path / "no-such-dir" / "out.pdf"

    with pytest.raises(HTTPException) as exc:
        sv.do_save(sv.SaveReq(path=str(bad_dest)))

    assert exc.value.status_code == 400
    assert sv.S["path"] == str(src)            # current document unchanged
    assert sv.S["dirty"] is True               # still unsaved
    assert not bad_dest.parent.exists() or not bad_dest.exists()


def test_save_in_place_keeps_working(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(_sample_bytes())
    sv.open_pdf(sv.OpenReq(path=str(p)))
    sv.S["dirty"] = True

    result = sv.do_save(sv.SaveReq())

    assert result["saved"] == str(p)
    assert sv.S["path"] == str(p)
    assert sv.S["dirty"] is False
    assert sv.file_info()["saved"] == sv.file_info()["disk"]


# ------------------------------------------------------------------ upload

def test_upload_opens_temp_copy_and_resets_state():
    resp = _client().post("/api/upload?filename=sample.pdf",
                          content=_sample_bytes(pages=2),
                          headers={"Content-Type": "application/pdf"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["uploaded"] is True
    assert body["pages"] == 2
    assert os.path.basename(body["path"]) == "sample.pdf"
    assert os.path.isfile(body["path"])
    assert sv.S["path"] == body["path"]       # temp copy is the open document
    assert sv.S["dirty"] is False
    assert sv.S["stat"]["missing"] is False


def test_upload_sanitizes_filename():
    resp = _client().post("/api/upload?filename=../../evil name?.pdf",
                          content=_sample_bytes())
    assert resp.status_code == 200, resp.text
    name = os.path.basename(resp.json()["path"])
    assert name.endswith(".pdf")
    assert "/" not in name and "\\" not in name
    assert ".." not in name and "?" not in name


def test_upload_response_matches_open_shape():
    pdf = _sample_bytes(pages=2)
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "src.pdf")
    with open(src, "wb") as fh:
        fh.write(pdf)
    open_keys = set(_client().post("/api/open",
                                   json={"path": src}).json().keys())

    body = _client().post("/api/upload?filename=a.pdf", content=pdf).json()

    assert set(body.keys()) == open_keys | {"uploaded"}   # open-shape + flag
    assert body["uploaded"] is True


def test_upload_same_filename_does_not_clobber():
    client = _client()
    first = client.post("/api/upload?filename=doc.pdf", content=_sample_bytes()).json()
    second = client.post("/api/upload?filename=doc.pdf", content=_sample_bytes()).json()

    assert first["path"] != second["path"]     # no silent overwrite
    assert os.path.isfile(first["path"]) and os.path.isfile(second["path"])


def test_invalid_upload_keeps_open_document(tmp_path):
    p = tmp_path / "keep.pdf"
    p.write_bytes(_sample_bytes())
    sv.open_pdf(sv.OpenReq(path=str(p)))
    before = dict(sv.S)

    resp = _client().post("/api/upload?filename=bad.pdf", content=b"not a pdf at all")

    assert resp.status_code == 400
    assert sv.S == before                     # document state untouched
    assert sv.S["path"] == str(p)
    assert sv.S["work"] == before["work"]


# ----------------------------------------------------------------- dialogs

def test_dialog_endpoints_501_without_desktop_bridge():
    client = _client()
    for method, url, payload in (("post", "/api/dialog/open", {}),
                                 ("post", "/api/dialog/save", {"suggested": "x.pdf"})):
        resp = getattr(client, method)(url, json=payload)
        assert resp.status_code == 501
        assert "detail" in resp.json()          # error shape the UI reads


def test_dialog_handler_error_returns_500_detail():
    def boom(*a, **k):
        raise RuntimeError("gtk exploded")

    sv.register_dialogs(open_fn=boom)
    resp = _client().post("/api/dialog/open", json={})
    assert resp.status_code == 500
    assert "gtk exploded" in resp.json()["detail"]


def test_dialog_open_uses_registered_bridge():
    sv.register_dialogs(open_fn=lambda: r"C:\tmp\picked.pdf")
    resp = _client().post("/api/dialog/open", json={})
    assert resp.status_code == 200
    assert resp.json() == {"path": r"C:\tmp\picked.pdf"}


def test_dialog_save_passes_suggestion_and_returns_path():
    seen: dict = {}

    def fake_save(suggested=None):
        seen["suggested"] = suggested
        return "/tmp/out.pdf"

    sv.register_dialogs(save_fn=fake_save)
    resp = _client().post("/api/dialog/save", json={"suggested": "doc.pdf"})
    assert resp.status_code == 200
    assert resp.json() == {"path": "/tmp/out.pdf"}
    assert seen["suggested"] == "doc.pdf"


def test_dialog_cancel_returns_none():
    sv.register_dialogs(open_fn=lambda: None, save_fn=lambda suggested=None: None)
    assert _client().post("/api/dialog/open", json={}).json() == {"path": None}
    assert _client().post("/api/dialog/save", json={}).json() == {"path": None}


# ------------------------------------------------- upload save destination

def _upload_sample(client) -> dict:
    return client.post("/api/upload?filename=sample.pdf",
                       content=_sample_bytes(pages=2),
                       headers={"Content-Type": "application/pdf"}).json()


def test_save_upload_doc_desktop_routes_through_save_dialog(tmp_path):
    client = _client()
    _upload_sample(client)
    picked = str(tmp_path / "chosen.pdf")
    seen: dict = {}
    sv.S["desktop"] = True

    def fake_save(suggested=None):
        seen["suggested"] = suggested
        return picked

    sv.register_dialogs(save_fn=fake_save)
    offered = os.path.basename(sv.S["path"])        # upload name before the switch
    result = sv.do_save(sv.SaveReq(path=None))

    assert seen["suggested"] == offered             # doc name offered to the dialog
    assert result["saved"] == picked
    assert sv.S["path"] == picked                   # document switched to real file
    assert sv.S["dirty"] is False


def test_save_upload_doc_dialog_cancel_returns_cancelled(tmp_path):
    client = _client()
    _upload_sample(client)
    sv.S["desktop"] = True
    sv.register_dialogs(save_fn=lambda suggested=None: None)   # user cancels

    result = sv.do_save(sv.SaveReq())

    assert result == {"saved": False, "cancelled": True}
    assert "sumipdf-upload-" in sv.S["path"]        # still the upload temp doc
    assert sv.S["dirty"] is False                   # nothing saved, state untouched


def test_save_upload_doc_browser_mode_is_400_save_as(tmp_path):
    client = _client()
    _upload_sample(client)                          # desktop flag stays False

    resp = client.post("/api/save", json={"path": None})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "別名保存してください"


def test_save_upload_doc_explicit_path_still_allowed(tmp_path):
    client = _client()
    _upload_sample(client)
    dest = tmp_path / "explicit.pdf"

    result = sv.do_save(sv.SaveReq(path=str(dest)))

    assert result["saved"] == str(dest)             # explicit path needs no dialog
    assert sv.S["path"] == str(dest)


def test_save_relative_path_anchors_to_document_dir(tmp_path, monkeypatch):
    src = tmp_path / "doc.pdf"
    src.write_bytes(_sample_bytes())
    sv.open_pdf(sv.OpenReq(path=str(src)))
    monkeypatch.chdir(tmp_path)                     # a stray CWD fallback would land here

    result = sv.do_save(sv.SaveReq(path="out.pdf"))

    assert result["saved"] == str(tmp_path / "out.pdf")
    assert (tmp_path / "out.pdf").is_file()
    assert sv.S["path"] == str(tmp_path / "out.pdf")


# ------------------------------------------------------------------ assets

def test_brand_icon_is_served_as_png():
    resp = _client().get("/assets/icon.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG")


def test_web_asset_is_served_inside_web_dir():
    assert os.path.isfile(os.path.join(sv._web_dir(), "index.html"))
    resp = _client().get("/assets/web/index.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_web_asset_missing_file_404():
    assert _client().get("/assets/web/nope-xyz.js").status_code == 404


@pytest.mark.parametrize("bad", [
    "../pyproject.toml",
    "../../pyproject.toml",
    "../../../etc/passwd",
    "/etc/hostname",
    "sub/../../README.md",
])
def test_web_asset_guard_rejects_outside_paths_directly(bad):
    """The route function itself never serves anything outside web/."""
    with pytest.raises(HTTPException) as exc:
        sv.web_asset(bad)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("path", [
    "/assets/web/../pyproject.toml",
    "/assets/web/%2e%2e/pyproject.toml",
    "/assets/web/..%2fpyproject.toml",
    "/assets/web/../resources/icon-1024.png",
])
def test_web_asset_http_traversal_rejected(path):
    resp = _client().get(path)
    assert resp.status_code == 404
    assert b"sumi-pdf" not in resp.content  # never leak repo files


# ------------------------------------------------------------ frozen assets

def test_asset_root_uses_meipass_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert sv._web_dir() == os.path.join(str(tmp_path), "web")
    assert sv._resource_dir() == os.path.join(str(tmp_path), "resources")


def test_asset_root_is_repo_root_in_dev():
    root = sv._asset_root()
    assert os.path.isdir(os.path.join(root, "web"))
    assert os.path.isdir(os.path.join(root, "resources"))
