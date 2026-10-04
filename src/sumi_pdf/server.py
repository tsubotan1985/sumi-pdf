"""FastAPI server + viewer UI. MIT engine (pypdfium2/pypdf/reportlab). Port 8765.

SumatraPDF-style live reload: files are opened from an in-memory copy (no file
handle kept -> other apps can save over the PDF anytime); the UI polls
/api/fileinfo and calls /api/reload when the disk file changes.
"""
from __future__ import annotations

import io
import mimetypes
import os
import re
import sys
import tempfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from pypdf import PdfReader

from . import crypto as C
from . import edit as E
from . import pages as PG
from . import pdfio as P
from . import redact as R
from . import stirling as ST
from . import textedit as T
from . import textlayer
from .ocr import detect_engines, ocr_page

app = FastAPI(title="SUMIPDF")
S: dict = {"src": None, "work": None, "path": None, "dirty": False, "stat": None,
           "n": 0, "pw": None, "desktop": False}


def _asset_root() -> str:
    """Directory that holds ``web/`` and ``resources/``.

    Frozen (PyInstaller): ``sys._MEIPASS`` -> the ``_internal`` bundle dir where
    ``--add-data "web;web"`` / ``"resources;resources"`` place the assets.
    Development: the repository root (three levels up from this file).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def _web_dir() -> str:
    return os.path.join(_asset_root(), "web")


def _resource_dir() -> str:
    return os.path.join(_asset_root(), "resources")


# Native (pywebview) file-dialog bridge, registered by the desktop shell.
# Absent in the plain browser server -> the dialog endpoints answer 501.
DIALOG_HANDLERS: dict = {}


def register_dialogs(open_fn=None, save_fn=None, desktop: bool = True) -> None:
    """Called by app.py once the pywebview window exists.

    ``open_fn() -> path|None`` and ``save_fn(suggested=None) -> path|None`` are
    plain callables that pop the native dialog (app.py marshals them onto the
    GUI thread).
    """
    if open_fn is not None:
        DIALOG_HANDLERS["open"] = open_fn
    if save_fn is not None:
        DIALOG_HANDLERS["save"] = save_fn
    S["desktop"] = desktop


def _dialog(kind: str):
    fn = DIALOG_HANDLERS.get(kind)
    if not S.get("desktop") or fn is None:
        raise HTTPException(501, "native file dialog is unavailable in browser mode")
    return fn



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
    p = os.path.join(_web_dir(), "index.html")
    if os.path.isfile(p):
        return FileResponse(p)
    return Response("web/index.html missing (run from repo root)", status_code=404)


@app.get("/assets/web/{filepath:path}")
def web_asset(filepath: str):
    """Serve files that resolve inside ``web/`` only (no traversal, no repo exposure)."""
    root = os.path.realpath(_web_dir())
    target = os.path.realpath(os.path.join(root, filepath))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:  # different drive letters on Windows
        inside = False
    if not inside or not os.path.isfile(target):
        raise HTTPException(404, "not found")
    mt, _ = mimetypes.guess_type(target)
    return FileResponse(target, media_type=mt or "application/octet-stream")


@app.get("/assets/icon.png")
def ui_icon():
    p = os.path.join(_resource_dir(), "icon-1024.png")
    if not os.path.isfile(p):
        raise HTTPException(404, "icon not found")
    return FileResponse(p, media_type="image/png")


class SaveDialogReq(BaseModel):
    suggested: str | None = None


@app.post("/api/dialog/open")
def dialog_open():
    fn = _dialog("open")
    try:
        path = fn()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"dialog failed: {e}")
    return {"path": str(path) if path else None}


@app.post("/api/dialog/save")
def dialog_save(req: SaveDialogReq):
    fn = _dialog("save")
    try:
        path = fn(suggested=req.suggested)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"dialog failed: {e}")
    return {"path": str(path) if path else None}


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_UPLOAD_DIR: str | None = None


def _upload_dir() -> str:
    global _UPLOAD_DIR
    if _UPLOAD_DIR is None:
        _UPLOAD_DIR = tempfile.mkdtemp(prefix="sumipdf-upload-")
    return _UPLOAD_DIR


def _safe_filename(filename: str) -> str:
    """Basename-only, characters restricted, forced .pdf extension."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ]", "_", name).strip(" .")
    if not name:
        name = "upload.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


@app.post("/api/upload")
async def upload_doc(request: Request, filename: str = "upload.pdf"):
    """Open a PDF supplied as a raw request body as an in-memory temp file.

    Invalid PDFs answer 400 and leave the currently open document untouched.
    """
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty upload")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    data = _load(body)  # validates; raises 400 before S is touched
    base = _safe_filename(filename)
    dest = os.path.join(_upload_dir(), base)
    if os.path.exists(dest):
        # Never clobber an in-flight upload: live-reload would fire on it.
        stem, ext = os.path.splitext(base)
        i = 1
        while os.path.exists(os.path.join(_upload_dir(), f"{stem}-{i}{ext}")):
            i += 1
        dest = os.path.join(_upload_dir(), f"{stem}-{i}{ext}")
    with open(dest, "wb") as fh:
        fh.write(data)
    S["src"] = S["work"] = data
    S["path"], S["dirty"], S["pw"] = dest, False, None
    S["stat"] = _disk_stat(dest)
    S["n"] = P.page_count(data)
    w, h = P.page_size(data, 0)
    return {"path": dest, "pages": S["n"], "size": [round(w, 1), round(h, 1)],
            "fonts": P.font_inventory(data), "stat": S["stat"], "uploaded": True}



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
    # Save-as: the new file becomes the current document (dirty cleared, stat synced).
    # Written via temp+replace like the in-place path so a failed save never
    # leaves a partial file at the chosen destination.
    try:
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(out) or None)
        os.close(fd)
        E.save_optimized(data, tmp)
        os.replace(tmp, out)
    except OSError as e:
        raise HTTPException(400, f"cannot save: {e}")
    S["path"] = out
    S["dirty"] = False
    S["stat"] = _disk_stat(out)
    return {"saved": out, "bytes": os.path.getsize(out), "stat": S["stat"]}


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


# ---------------- image pixel redaction (true data removal) ----------------

class ImgRedactReq(BaseModel):
    page: int
    rects: list[list[float]]
    fill: list[int] = [255, 255, 255]


@app.post("/api/img-redact")
def img_redact(req: ImgRedactReq):
    """Blank pixels inside rects in the page's embedded images (destructive)."""
    data = _require()
    if not 0 <= req.page < S["n"]:
        raise HTTPException(404, "page out of range")
    from . import imgredact as IR
    r = IR.remove_pixels(data, req.page, [tuple(x) for x in req.rects],
                         fill=tuple(req.fill))
    if r.get("bytes"):
        S["work"], S["dirty"] = r["bytes"], True
    return {"edited": r["edited"], "rects": r["rects"]}


# ---------------- Stirling-parity utilities ----------------

class CompressReq(BaseModel):
    out_path: str | None = None
    image_quality: int = 70
    grayscale: bool = False


@app.post("/api/compress")
def do_compress(req: CompressReq):
    data = _require()
    out = req.out_path or (os.path.splitext(S["path"] or "out")[0] + "-c.pdf")
    return ST.compress(data, out, image_quality=req.image_quality,
                       grayscale=req.grayscale)


class WatermarkReq(BaseModel):
    page: int = 0
    text: str = "CONFIDENTIAL"
    opacity: float = 0.15
    angle: int = 45
    size: float = 60.0


@app.post("/api/watermark")
def do_watermark(req: WatermarkReq):
    data = _require()
    S["work"] = ST.watermark(data, req.page, req.text, opacity=req.opacity,
                             angle=req.angle, size=req.size)
    S["dirty"] = True
    return {"watermarked": req.text}


class PageNumbersReq(BaseModel):
    out_path: str | None = None
    fmt: str = "{n} / {total}"
    pos: str = "bottom-center"
    size: float = 10.0
    start: int = 1


@app.post("/api/page-numbers")
def do_page_numbers(req: PageNumbersReq):
    data = _require()
    out = req.out_path or (os.path.splitext(S["path"] or "out")[0] + "-num.pdf")
    return ST.page_numbers(data, out, fmt=req.fmt, pos=req.pos, size=req.size,
                           start=req.start)


@app.get("/api/metadata")
def get_meta():
    return ST.get_metadata(_require())


class MetaReq(BaseModel):
    out_path: str | None = None
    fields: dict


@app.post("/api/metadata")
def set_meta(req: MetaReq):
    data = _require()
    out = req.out_path or (os.path.splitext(S["path"] or "out")[0] + "-meta.pdf")
    return ST.set_metadata(data, out, **req.fields)


class SanitizeReq(BaseModel):
    out_path: str | None = None
    remove_js: bool = True
    remove_embedded_files: bool = True
    remove_metadata: bool = True
    remove_links: bool = False
    remove_annotations: bool = False


@app.post("/api/sanitize")
def do_sanitize(req: SanitizeReq):
    data = _require()
    out = req.out_path or (os.path.splitext(S["path"] or "out")[0] + "-clean.pdf")
    return ST.sanitize(data, out, remove_js=req.remove_js,
                       remove_embedded_files=req.remove_embedded_files,
                       remove_metadata=req.remove_metadata,
                       remove_links=req.remove_links,
                       remove_annotations=req.remove_annotations)


def main():
    import uvicorn
    port = int(os.environ.get("SUMI_PORT", "8765"))
    print(f"SUMIPDF server: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
