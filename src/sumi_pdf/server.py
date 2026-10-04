"""FastAPI server + minimal viewer UI. Run: sumi-server (port 8642)."""
from __future__ import annotations

import os

import pymupdf as fitz
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import fonts as F
from . import redact as R
from . import textedit as T
from .ocr import detect_engines, ocr_page

app = FastAPI(title="Sumi PDF")
S: dict = {"doc": None, "path": None}

_WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "web")


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
        doc = fitz.open(path)
    except Exception as e:
        raise HTTPException(400, f"cannot open: {e}")
    if doc.needs_pass:
        raise HTTPException(400, "encrypted PDF not supported yet")
    S["doc"], S["path"] = doc, path
    return {"path": path, "pages": len(doc),
            "size": [round(doc[0].rect.width, 1), round(doc[0].rect.height, 1)],
            "fonts": T.doc_fonts(doc)}


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
    return {"saved": out, "bytes": os.path.getsize(out)}


@app.get("/api/fonts")
def get_fonts():
    return F.inventory()


@app.get("/api/ocr-engines")
def ocr_engines():
    return detect_engines()


@app.post("/api/ocr/{pno}")
def do_ocr(pno: int):
    if not S["path"]:
        raise HTTPException(400, "no document open")
    try:
        return ocr_page(S["path"], pno)
    except RuntimeError as e:
        raise HTTPException(501, str(e))


def main():
    import uvicorn
    port = int(os.environ.get("SUMI_PORT", "8765"))
    print(f"Sumi PDF server: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
