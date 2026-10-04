"""Text edit: locate a needle in the text layer, remove it from the PDF data,
re-insert replacement text at the same baseline with a resolvable font.

v0 scope: horizontal text, per-page, all occurrences, any replacement length
(longer text extends to the right and may overlap following chars - retypeset
comes later). Vertical text raises NotSupportedError.
"""
from __future__ import annotations

import pymupdf as fitz

from . import fonts as F


class NotSupportedError(ValueError):
    pass


def find_occurrences(page: fitz.Page, needle: str) -> list[dict]:
    """Char-level occurrences with union bbox + first-char origin (baseline)."""
    if not needle:
        return []
    out = []
    raw = page.get_text("rawdict")
    for b in raw["blocks"]:
        for l in b.get("lines", []):
            direction = l.get("dir", (1.0, 0.0))
            for s in l["spans"]:
                chars = s["chars"]
                txt = "".join(c["c"] for c in chars).replace("\xa0", " ")
                needle_n = needle.replace("\xa0", " ")
                start = 0
                while True:
                    i = txt.find(needle_n, start)
                    if i < 0:
                        break
                    sub = chars[i: i + len(needle)]
                    rect = fitz.Rect(min(c["bbox"][0] for c in sub),
                                     min(c["bbox"][1] for c in sub),
                                     max(c["bbox"][2] for c in sub),
                                     max(c["bbox"][3] for c in sub))
                    out.append({"bbox": rect,
                                "origin": tuple(sub[0]["origin"]),
                                "size": float(s["size"]),
                                "font": s["font"],
                                "dir": direction})
                    start = i + len(needle)
    return out


def replace_text(doc: fitz.Document, page_no: int, needle: str, replacement: str,
                 font_name: str | None = None) -> dict:
    """Remove `needle` occurrences on page and insert `replacement` in place.

    font_name: alias (ms-mincho/ipaexgothic/...) or a font file path.
    Missing/None -> bundled gothic. Returns info for assertions/UI.
    """
    if needle == replacement:
        raise ValueError("needle == replacement")
    page = doc[page_no]
    occ = find_occurrences(page, needle)
    if not occ:
        return {"found": 0, "replaced": 0}
    for o in occ:
        if tuple(round(v, 3) for v in o["dir"]) not in ((1.0, 0.0), (1, 0)):
            raise NotSupportedError("vertical text replacement is v1 scope")
    res = F.resolve(font_name) if font_name else {"path": None}
    fontfile = res.get("path") or F.default_font("gothic")
    for o in occ:
        page.add_redact_annot(fitz.Rect(o["bbox"]))
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    alias = "SumiF"
    done = 0
    for o in occ:
        try:
            page.insert_text(fitz.Point(o["origin"]), replacement,
                             fontsize=o["size"], fontname=alias, fontfile=fontfile)
            done += 1
        except Exception:
            # fallback to bundled gothic if system font failed (e.g. ttc edge)
            fb = F.default_font("gothic")
            page.insert_text(fitz.Point(o["origin"]), replacement,
                             fontsize=o["size"], fontname=alias + "FB", fontfile=fb)
            done += 1
    return {"found": len(occ), "replaced": done, "font": fontfile,
            "source": res.get("source", "bundled")}


def doc_fonts(doc: fitz.Document) -> list[dict]:
    """Embedded fonts across the doc with embed flag (deduped)."""
    seen = {}
    for pno in range(len(doc)):
        for xref, ext, ftype, basefont, refname, enc in doc.get_page_fonts(pno):
            name = basefont.split("+")[-1] if "+" in basefont else basefont
            key = (name, ftype)
            if key not in seen:
                seen[key] = {"name": name, "type": ftype,
                             "embedded": ext != "n/a", "pages": []}
            seen[key]["pages"].append(pno)
    for v in seen.values():
        v["pages"] = sorted(set(v["pages"]))
    return list(seen.values())
