"""FastAPI server + viewer UI. MIT engine (pypdfium2/pypdf/reportlab). Port 8765.

SumatraPDF-style live reload: files are opened from an in-memory copy (no file
handle kept -> other apps can save over the PDF anytime); the UI polls
/api/fileinfo and calls /api/reload when the disk file changes.
"""
from __future__ import annotations

import io
import os
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from pypdf import PdfReader

from . import crypto as C
from . import edit as E
from . import pages as PG
from . import pdfio as P
from . import redact as R
from . import textedit as T
from . import textlayer
from .ocr import detect_engines, ocr_page

app = FastAPI(title="SUMIPDF")
S: dict = {"src": None, "work": None, "path": None, "dirty": False, "stat": None,
           "n": 0, "pw": None}

_WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "web")


def _disk_stat(path: str) -> dict:
    try:
        st = os.stat(path)
        return {"mtime": round(st.st_mtime, 3), "size": st.st_size, "missing": False}
    except OSError:
        return {"mtime": 0, "size": 0, "missing": True}


def _load(data: bytes, password: str | None = None) -> bytes:
    """Validate + decrypt-if-needed; returns plain bytes to work with."""
    try:
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            if not password:
                raise HTTPException(400, "password required")
            res = r.decrypt(password)
            if res == 0:
                raise HTTPException(400, "wrong password")
            w = __import__("pypdf").PdfWriter()
            w.append(r)
            buf = io.BytesIO()
            w.write(buf)
            return buf.getvalue()
        P.page_count(data)  # pdfium sanity check
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"cannot open: {e}")


def _open_nolock(path: str, password: str | None = None) -> bytes:
    """In-memory copy: no persistent handle, other apps can save over it anytime."""
    with open(path, "rb") as fh:
        return _load(fh.read(), password or S.get("pw"))


def _require() -> bytes:
    if S["work"] is None:
        raise HTTPException(400, "no document open")
    return S["work"]


def _tmpfile() -> str:
    fd, p = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    with open(p, "wb") as fh:
        fh.write(S["work"])
    return p


class OpenReq(BaseModel):
    path: str
    password: str | None = None


class RedactReq(BaseModel):
    page: int
    rects: list[list[float]]
    match_bg: bool = True
    fill: list[float] = [1.0, 1.0, 1.0]


class ReplaceReq(BaseModel):
    page: int
    find: str
    replace: str
    size: float | None = None


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
    data = _open_nolock(path, req.password)
    S["src"] = S["work"] = data
    S["path"], S["dirty"], S["pw"] = path, False, req.password
    S["stat"] = _disk_stat(path)
    S["n"] = P.page_count(data)
    w, h = P.page_size(data, 0)
    return {"path": path, "pages": S["n"], "size": [round(w, 1), round(h, 1)],
            "fonts": P.font_inventory(data), "stat": S["stat"]}


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
    data = _open_nolock(S["path"])
    n = P.page_count(data)
    page = max(0, min(req.page, n - 1))
    S["src"] = S["work"] = data
    S["n"] = n
    S["stat"] = _disk_stat(S["path"])
    return {"pages": n, "page": page, "stat": S["stat"]}


@app.get("/api/page/{pno}")
def get_page(pno: int, dpi: int = 120):
    data = _require()
    if not 0 <= pno < S["n"]:
        raise HTTPException(404, "page out of range")
    try:
        png = P.render_png(data, pno, dpi=max(30, min(dpi, 400)), password=S["pw"])
    except Exception as e:
        raise HTTPException(500, f"render failed: {e}")
    return Response(png, media_type="image/png")


@app.get("/api/extract/{pno}")
def extract_text(pno: int):
    data = _require()
    if not 0 <= pno < S["n"]:
        raise HTTPException(404, "page out of range")
    return {"page": pno, "text": P.extract_text(data, pno)}


@app.post("/api/redact")
def do_redact(req: RedactReq):
    data = _require()
    if not 0 <= req.page < S["n"]:
        raise HTTPException(404, "page out of range")
    rects = [tuple(r) for r in req.rects]
    out = R.redact(data, req.page, rects, match_bg=req.match_bg,
                   fill=tuple(req.fill))
    S["work"], S["dirty"] = out["bytes"], True
    return {"applied": {"dropped": out["dropped"], "filled": out["filled"]}}


@app.post("/api/replace")
def do_replace(req: ReplaceReq):
    data = _require()
    if not 0 <= req.page < S["n"]:
        raise HTTPException(404, "page out of range")
    try:
        r = T.replace(data, req.page, req.find, req.replace, req.size)
    except Exception as e:
        raise HTTPException(400, str(e))
    S["work"], S["dirty"] = r["bytes"], True
    return {"replaced": r["replaced"], "dropped": r["dropped"]}


@app.post("/api/save")
def do_save(req: SaveReq):
    data = _require()
    out = req.path or S["path"]
    if out == S["path"]:
        # atomic-ish: write temp then replace (keeps no-lock guarantee)
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(out) or None)
        os.close(fd)
        E.save_optimized(data, tmp)
        os.replace(tmp, out)
        S["dirty"] = False
        S["stat"] = _disk_stat(out)
        return {"saved": out, "bytes": os.path.getsize(out), "stat": S["stat"]}
    E.save_optimized(data, out)
    return {"saved": out, "bytes": os.path.getsize(out), "stat": _disk_stat(out)}


@app.get("/api/fonts")
def get_fonts():
    return P.font_inventory(S["work"]) if S["work"] else []


@app.get("/api/ocr-engines")
def ocr_engines():
    return detect_engines()


@app.post("/api/ocr/{pno}")
def do_ocr(pno: int, lang: str = "auto"):
    _require()
    try:
        return ocr_page(_tmpfile(), pno, lang=lang)
    except RuntimeError as e:
        raise HTTPException(501, str(e))


class OcrLayerReq(BaseModel):
    page: int
    lang: str = "auto"


@app.post("/api/ocr-layer")
def do_ocr_layer(req: OcrLayerReq):
    """OCR the page then write an invisible text layer into the open document."""
    data = _require()
    try:
        r = ocr_page(_tmpfile(), req.page, lang=req.lang)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    out = textlayer.add_layer_doc(data, req.page, r["words"])
    S["work"], S["dirty"] = out, True
    return {"words": len(r["words"]), "lang": r["lang"], "text_head": r["text"][:200]}


class EncryptReq(BaseModel):
    user_pw: str = ""
    owner_pw: str = ""
    algorithm: str = "AES-256"
    out_path: str | None = None
    allow: dict = {}


class DecryptReq(BaseModel):
    password: str = ""
    out_path: str | None = None


def _allow_kwargs(a: dict) -> dict:
    return {k: bool(a.get(k, True)) for k in
            ("print_", "copy", "modify", "annotate", "forms", "assemble", "print_high")}


@app.post("/api/encrypt")
def do_encrypt(req: EncryptReq):
    data = _require()
    src = _tmpfile()
    out = req.out_path or (os.path.splitext(S["path"] or "out.pdf")[0] + "-enc.pdf")
    try:
        return C.encrypt_pdf(src, out, req.user_pw, req.owner_pw or None,
                             req.algorithm, **_allow_kwargs(req.allow))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/decrypt")
def do_decrypt(req: DecryptReq):
    if not S["path"]:
        raise HTTPException(400, "no document open")
    out = req.out_path or (os.path.splitext(S["path"])[0] + "-dec.pdf")
    try:
        return C.decrypt_pdf(S["path"], out, req.password)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


# ---------------- page operations (Stirling parity) ----------------

class RotateReq(BaseModel):
    page: int
    deg: int = 90


@app.post("/api/pages/rotate")
def pages_rotate(req: RotateReq):
    data = _require()
    if not 0 <= req.page < S["n"]:
        raise HTTPException(404, "page out of range")
    S["work"] = PG.rotate(data, req.page, req.deg)
    S["dirty"] = True
    return {"rotated": [req.page, req.deg]}


class PagesReq(BaseModel):
    pnos: list[int]


@app.post("/api/pages/delete")
def pages_delete(req: PagesReq):
    data = _require()
    S["work"] = PG.delete_pages(data, req.pnos)
    S["n"] = P.page_count(S["work"])
    S["dirty"] = True
    return {"pages": S["n"]}


@app.post("/api/pages/extract")
def pages_extract(req: PagesReq):
    data = _require()
    out = S["path"] and (os.path.splitext(S["path"])[0] + "-extract.pdf") or "extract.pdf"
    out_b = PG.extract_pages(data, req.pnos)
    with open(out, "wb") as fh:
        fh.write(out_b)
    return {"out": out, "pages": len(req.pnos), "bytes": os.path.getsize(out)}


class InsertReq(BaseModel):
    path: str
    at: int | None = None


@app.post("/api/insert")
def pages_insert(req: InsertReq):
    data = _require()
    if not os.path.isfile(req.path):
        raise HTTPException(404, f"not found: {req.path}")
    S["work"] = PG.insert_pdf(data, req.path, req.at)
    S["n"] = P.page_count(S["work"])
    S["dirty"] = True
    return {"pages": S["n"]}


class MergeReq(BaseModel):
    paths: list[str]
    out: str


@app.post("/api/merge")
def pages_merge(req: MergeReq):
    for p in req.paths:
        if not os.path.isfile(p):
            raise HTTPException(404, f"not found: {p}")
    return PG.merge_pdfs(req.paths, req.out)


class SplitReq(BaseModel):
    out_dir: str | None = None
    base: str | None = None


@app.post("/api/split")
def pages_split(req: SplitReq):
    data = _require()
    out_dir = req.out_dir or (os.path.dirname(S["path"]) if S["path"] else ".")
    base = req.base or (os.path.splitext(os.path.basename(S["path"]))[0] if S["path"] else "page")
    return {"files": PG.split(data, out_dir, base)}


def main():
    import uvicorn
    port = int(os.environ.get("SUMI_PORT", "8765"))
    print(f"SUMIPDF server: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
