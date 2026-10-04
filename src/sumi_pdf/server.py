"""FastAPI server + minimal viewer UI. Run: sumi-server (port 8765).

SumatraPDF-style live reload: files are opened from an in-memory copy (no file
handle kept -> other apps can save over the PDF anytime); the UI polls
/api/fileinfo and calls /api/reload when the disk file changes.
"""
from __future__ import annotations

import os

import pymupdf as fitz
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import fonts as F
from . import redact as R
from . import textedit as T
from . import textlayer
from . import crypto as C
from .ocr import detect_engines, ocr_page

app = FastAPI(title="SUMIPDF")
S: dict = {"doc": None, "path": None, "dirty": False, "stat": None}

_WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "web")


def _disk_stat(path: str) -> dict:
    try:
        st = os.stat(path)
        return {"mtime": round(st.st_mtime, 3), "size": st.st_size, "missing": False}
    except OSError:
        return {"mtime": 0, "size": 0, "missing": True}


def _open_nolock(path: str) -> fitz.Document:
    """Open from an in-memory copy: no persistent file handle, so other
    applications can overwrite/rename/delete the PDF at any time."""
    with open(path, "rb") as fh:
        data = fh.read()
    return fitz.open(stream=data, filetype="pdf")


class OpenReq(BaseModel):
    path: str


class RedactReq(BaseModel):
    page: int
    rects: list[list[float]]
    match_bg: bool = True
    fill: list[int] = [0, 0, 0]
    images: str = "pixels"
    graphics: str = "none"


class ReplaceReq(BaseModel):
    page: int
    find: str
    replace: str
    font: str | None = None


class SaveReq(BaseModel):
    path: str | None = None
    subset: bool = True


@app.get("/")
def index():
    p = os.path.join(_WEB, "index.html")
    if os.path.isfile(p):
        return FileResponse(p)
    return Response("web/index.html missing (run from repo root)", status_code=404)


@app.post("/api/open")
def open_pdf(req: OpenReq):
    path = req.path
    if not os.path.isabs(path) and S["path"]:
        path = os.path.join(os.path.dirname(S["path"]), req.path)
    if not os.path.isfile(path):
        raise HTTPException(404, f"not found: {path}")
    try:
        doc = _open_nolock(path)
    except Exception as e:
        raise HTTPException(400, f"cannot open: {e}")
    if doc.needs_pass:
        raise HTTPException(400, "encrypted PDF not supported yet")
    S["doc"], S["path"], S["dirty"] = doc, path, False
    S["stat"] = _disk_stat(path)
    return {"path": path, "pages": len(doc),
            "size": [round(doc[0].rect.width, 1), round(doc[0].rect.height, 1)],
            "fonts": T.doc_fonts(doc), "stat": S["stat"]}


@app.get("/api/fileinfo")
def file_info():
    """Current disk stat of the open file + dirty flag (polled by the UI)."""
    if not S["path"]:
        raise HTTPException(400, "no document open")
    st = _disk_stat(S["path"])
    return {"path": S["path"], "dirty": S["dirty"], "disk": st, "saved": S["stat"]}


class ReloadReq(BaseModel):
    page: int = 0


@app.post("/api/reload")
def reload_doc(req: ReloadReq):
    """Re-open the file from disk (SumatraPDF-style external-update reload).
    Refuses (409) when there are unsaved in-memory edits."""
    if not S["path"]:
        raise HTTPException(400, "no document open")
    if S["dirty"]:
        raise HTTPException(409, "unsaved edits in memory; save or discard first")
    if not os.path.isfile(S["path"]):
        raise HTTPException(404, f"file gone: {S['path']}")
    try:
        doc = _open_nolock(S["path"])
    except Exception as e:
        raise HTTPException(400, f"cannot reopen: {e}")
    if doc.needs_pass:
        raise HTTPException(400, "encrypted PDF not supported yet")
    page = max(0, min(req.page, len(doc) - 1))
    S["doc"] = doc
    S["stat"] = _disk_stat(S["path"])
    return {"pages": len(doc), "page": page, "stat": S["stat"]}


@app.get("/api/page/{pno}")
def get_page(pno: int, dpi: int = 120):
    if not S["doc"]:
        raise HTTPException(400, "no document open")
    if not 0 <= pno < len(S["doc"]):
        raise HTTPException(404, "page out of range")
    pm = S["doc"][pno].get_pixmap(dpi=max(30, min(dpi, 400)))
    return Response(pm.tobytes("png"), media_type="image/png")


@app.post("/api/redact")
def do_redact(req: RedactReq):
    if not S["doc"]:
        raise HTTPException(400, "no document open")
    infos = R.redact(S["doc"][req.page], req.rects,
                     match_bg=req.match_bg, fill=tuple(req.fill),
                     images=req.images, graphics=req.graphics)
    S["dirty"] = True
    return {"applied": infos}


@app.post("/api/replace")
def do_replace(req: ReplaceReq):
    if not S["doc"]:
        raise HTTPException(400, "no document open")
    try:
        r = T.replace_text(S["doc"], req.page, req.find, req.replace, req.font)
    except T.NotSupportedError as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    S["dirty"] = True
    return r


@app.post("/api/save")
def do_save(req: SaveReq):
    if not S["doc"]:
        raise HTTPException(400, "no document open")
    out = req.path or S["path"]
    if req.subset:
        try:
            S["doc"].subset_fonts()
        except Exception:
            pass
    S["doc"].save(out, garbage=4, deflate=True)
    if out == S["path"]:
        S["dirty"] = False
        S["stat"] = _disk_stat(out)
    return {"saved": out, "bytes": os.path.getsize(out), "stat": S["stat"]}


@app.get("/api/fonts")
def get_fonts():
    return F.inventory()


@app.get("/api/ocr-engines")
def ocr_engines():
    return detect_engines()


@app.post("/api/ocr/{pno}")
def do_ocr(pno: int, lang: str = "auto"):
    if not S["path"]:
        raise HTTPException(400, "no document open")
    try:
        return ocr_page(S["path"], pno, lang=lang)
    except RuntimeError as e:
        raise HTTPException(501, str(e))


class OcrLayerReq(BaseModel):
    page: int
    lang: str = "auto"


@app.post("/api/ocr-layer")
def do_ocr_layer(req: OcrLayerReq):
    """OCR the page then write an invisible text layer into the open document."""
    if not S["doc"]:
        raise HTTPException(400, "no document open")
    try:
        r = ocr_page(S["path"], req.page, lang=req.lang)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    n = textlayer.add_layer_doc(S["doc"], req.page, r["words"])
    S["dirty"] = True
    return {"words": len(r["words"]), "inserted": n, "lang": r["lang"],
            "text_head": r["text"][:200]}


class EncryptReq(BaseModel):
    user_pw: str = ""
    owner_pw: str = ""
    algorithm: str = "aes-256"
    out_path: str | None = None
    allow: dict = {}


class DecryptReq(BaseModel):
    password: str = ""
    out_path: str | None = None


@app.post("/api/encrypt")
def do_encrypt(req: EncryptReq):
    if not S["path"]:
        raise HTTPException(400, "no document open")
    try:
        return C.encrypt_pdf(S["path"], req.out_path or "", req.user_pw, req.owner_pw,
                             req.algorithm, req.allow)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/decrypt")
def do_decrypt(req: DecryptReq):
    if not S["path"]:
        raise HTTPException(400, "no document open")
    try:
        return C.decrypt_pdf(S["path"], req.out_path or "", req.password)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


def main():
    import uvicorn
    port = int(os.environ.get("SUMI_PORT", "8765"))
    print(f"SUMIPDF server: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
