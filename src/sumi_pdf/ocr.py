"""OCR: pluggable local engines. v0 auto-detects (in priority order):

1. NDLOCR-Lite CLI (ndl_lab_ocr / ndlocr_lite_gui.exe install dir via SUMI_OCRLITE_DIR)
2. tesseract binary (jpn/jpn_vert traineddata) via pytesseract or raw CLI
3. none -> clear error telling how to plug one in

The engine contract: ocr_page(pdf_path, page_no) -> {"text": str, "engine": str}
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pymupdf as fitz


def detect_engines() -> dict:
    out = {"ndlocr_lite": None, "tesseract": None}
    base = os.environ.get("SUMI_OCRLITE_DIR", "")
    if base and os.path.isdir(base):
        for exe in ("ndlocr_lite_cli.exe", "ndlocr_lite_cli", "ndlocr_lite_gui.exe"):
            p = os.path.join(base, exe)
            if os.path.isfile(p):
                out["ndlocr_lite"] = p
                break
    tess = shutil.which("tesseract")
    if tess:
        out["tesseract"] = tess
    return out


def _render_png(doc, page_no: int, dpi: int = 300) -> str:
    pm = doc[page_no].get_pixmap(dpi=dpi)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    pm.save(path)
    return path


def ocr_page(pdf_path: str, page_no: int, lang: str = "jpn+jpn_vert") -> dict:
    eng = detect_engines()
    doc = fitz.open(pdf_path)
    try:
        png = _render_png(doc, page_no)
    finally:
        doc.close()
    try:
        if eng["tesseract"]:
            cmd = [eng["tesseract"], png, "stdout", "-l", lang, "--psm", "1"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:300])
            return {"text": r.stdout, "engine": "tesseract"}
        if eng["ndlocr_lite"]:
            outdir = tempfile.mkdtemp(prefix="sumi_ocr_")
            cmd = [eng["ndlocr_lite"], png, "-o", outdir]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            txt = ""
            for root, _dirs, files in os.walk(outdir):
                for f in files:
                    if f.endswith((".txt", ".md")):
                        txt += open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            return {"text": txt, "engine": "ndlocr-lite"}
        raise RuntimeError(
            "no OCR engine found. Install tesseract (with jpn/jpn_vert) "
            "or NDLOCR-Lite and set SUMI_OCRLITE_DIR")
    finally:
        try:
            os.remove(png)
        except OSError:
            pass
