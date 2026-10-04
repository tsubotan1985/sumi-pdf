"""OCR: local engines. v0.2 ships tesseract (jpn + jpn_vert, tessdata_fast bundled).

Engine detection order (tesseract):
  1. SUMI_TESSERACT env (path to binary)
  2. shutil.which("tesseract")
  3. ~/.pixi/bin/tesseract            (pixi global install)
  4. Windows: C:\\Program Files\\Tesseract-OCR\\tesseract.exe (+ x86)

tessdata: repo tools/tessdata (jpn/jpn_vert/eng/osd, tessdata_fast) used via
--tessdata-dir when present; otherwise the binary's default.

NDLOCR-Lite: optional experimental hook via SUMI_OCRLITE_SRC (checkout of
ndl-lab/ndlocr-lite with deps installed); run through its src/ocr.py.
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile

import pymupdf as fitz

_HERE = os.path.dirname(os.path.abspath(__file__))
_WIN_TESS_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _repo_dir() -> str:
    d = os.path.dirname(_HERE)          # src/sumi_pdf -> src
    for _ in range(3):
        if os.path.isdir(os.path.join(d, "tools", "tessdata")):
            return d
        d = os.path.dirname(d)
    me = getattr(sys, "_MEIPASS", None)
    if me and os.path.isdir(os.path.join(me, "tools", "tessdata")):
        return me
    return d


def detect_engines() -> dict:
    out = {"tesseract": None, "tessdata": None, "ndlocr_src": None}
    cand = os.environ.get("SUMI_TESSERACT")
    if cand and os.path.isfile(cand):
        out["tesseract"] = cand
    if not out["tesseract"]:
        out["tesseract"] = shutil.which("tesseract")
    if not out["tesseract"]:
        p = os.path.expanduser("~/.pixi/bin/tesseract")
        if os.path.isfile(p):
            out["tesseract"] = p
    if not out["tesseract"]:
        for p in _WIN_TESS_PATHS:
            if os.path.isfile(p):
                out["tesseract"] = p
                break
    base = os.environ.get("SUMI_TESSDATA") or os.path.join(_repo_dir(), "tools", "tessdata")
    if os.path.isfile(os.path.join(base, "jpn.traineddata")):
        out["tessdata"] = base
    src = os.environ.get("SUMI_OCRLITE_SRC")
    if src and os.path.isfile(os.path.join(src, "ocr.py")):
        out["ndlocr_src"] = src
    return out


def _render_png(doc, page_no: int, dpi: int = 300) -> str:
    pm = doc[page_no].get_pixmap(dpi=dpi)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    pm.save(path)
    return path


def _run_tsv(tess: str, png: str, lang: str, tessdata: str | None) -> str:
    cmd = [tess, png, "stdout", "-l", lang, "--psm", "1", "tsv"]
    if tessdata:
        cmd += ["--tessdata-dir", tessdata]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"tesseract failed: {r.stderr.strip()[:300]}")
    return r.stdout


def parse_tsv(tsv: str, dpi: int = 300) -> tuple[str, list[dict]]:
    """Parse tesseract TSV -> (text, words with PDF-pt bboxes at 72dpi base)."""
    words = []
    lines = []
    sc = 72.0 / dpi
    rows = list(csv.reader(io.StringIO(tsv), delimiter="\t"))
    for row in rows[1:]:
        if len(row) < 12 or row[0] != "5":  # level 5 = word
            continue
        try:
            conf = float(row[10])
        except ValueError:
            continue
        text = row[11].strip()
        if not text or conf < 0:
            continue
        l, t, w, h = (int(row[6]), int(row[7]), int(row[8]), int(row[9]))
        words.append({"text": text, "conf": round(conf, 1),
                      "bbox": [round(l * sc, 1), round(t * sc, 1),
                               round((l + w) * sc, 1), round((t + h) * sc, 1)]})
        lines.append((int(row[2]), int(row[4]), text))
    # rebuild reading order text from line grouping
    ordered, last = [], None
    for _b, ln, txt in lines:
        if last is not None and ln != last:
            ordered.append("\n")
        ordered.append(txt + " ")
        last = ln
    return "".join(ordered).replace(" \n", "\n").strip(), words


def _mean_conf(words) -> float:
    return (sum(w["conf"] for w in words) / len(words)) if words else 0.0


def ocr_page(pdf_path: str, page_no: int, lang: str = "auto", dpi: int = 300) -> dict:
    eng = detect_engines()
    if not eng["tesseract"]:
        raise RuntimeError(
            "tesseract not found. Install: WSL `~/.pixi/bin/pixi global install tesseract` "
            "/ Windows `winget install UB-Mannheim.TesseractOCR`. "
            "jpn/jpn_vert traineddata: repo tools/tessdata (bundled).")
    doc = fitz.open(pdf_path)
    try:
        png = _render_png(doc, page_no, dpi)
    finally:
        doc.close()
    tess, td = eng["tesseract"], eng["tessdata"]
    try:
        if lang in ("jpn", "jpn_vert"):
            tsv = _run_tsv(tess, png, lang, td)
            text, words = parse_tsv(tsv, dpi)
            return {"text": text, "words": words, "engine": "tesseract", "lang": lang}
        # auto: decide horizontal/vertical by confidence duel (jpn first)
        tsv = _run_tsv(tess, png, "jpn", td)
        text, words = parse_tsv(tsv, dpi)
        best = ("jpn", text, words, _mean_conf(words))
        if len(words) == 0 or best[3] < 70:
            tsv2 = _run_tsv(tess, png, "jpn_vert", td)
            text2, words2 = parse_tsv(tsv2, dpi)
            if _mean_conf(words2) > best[3]:
                best = ("jpn_vert", text2, words2, _mean_conf(words2))
        return {"text": best[1], "words": best[2], "engine": "tesseract", "lang": best[0]}
    finally:
        try:
            os.remove(png)
        except OSError:
            pass
