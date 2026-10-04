r"""Font registry: bundled fonts + system font fallback + name normalization.

- bundled: repo fonts/ (redistributable licenses only: IPA / OFL / Liberation)
- system:  Windows C:\Windows\Fonts etc. referenced at runtime (never redistributed)
- mojibake: PDFs made by some tools store SJIS double-byte names with lead bytes
  stripped ("lr¾©" = ＭＳ明朝, "lrSVbN" = ＭＳゴシック); known garbles mapped.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_fonts_dir() -> str:
    # dev layout: <repo>/fonts  (src/sumi_pdf/fonts.py -> up 3)
    d = os.path.dirname(_HERE)
    for _ in range(4):
        cand = os.path.join(d, "fonts")
        if os.path.isdir(cand):
            return cand
        d = os.path.dirname(d)
    if getattr(sys, "_MEIPASS", None):  # PyInstaller
        cand = os.path.join(sys._MEIPASS, "fonts")
        if os.path.isdir(cand):
            return cand
    return ""


FONTS_DIR = _find_fonts_dir()

GARBLED = {
    "lr¾©": "msmincho",
    "lrSVbN": "msgothic",
    "‚l‚r–¾’©": "msmincho",
    "‚l‚r‚oƒSƒVƒbƒN": "mspgothic",
    "‚l‚r‚o–¾’©": "mspmincho",
}

# normalized key -> {"system": [candidates], "bundled": filename}
ALIAS: dict[str, dict] = {
    "msmincho":       {"system": ["msmincho.ttc"],                     "bundled": "ipaexm.ttf"},
    "mspmincho":      {"system": ["msmincho.ttc"],                     "bundled": "ipaexm.ttf"},
    "msgothic":       {"system": ["msgothic.ttc"],                     "bundled": "ipaexg.ttf"},
    "mspgothic":      {"system": ["msgothic.ttc"],                     "bundled": "ipaexg.ttf"},
    "msuigothic":     {"system": ["msgothic.ttc"],                     "bundled": "ipaexg.ttf"},
    "yumincho":       {"system": ["YuMin_1.ttc", "yumin.ttf", "YuMincho.ttc"], "bundled": "ipaexm.ttf"},
    "yumindemibold":  {"system": ["YuMin_1.ttc", "yumin.ttf"],         "bundled": "ipaexm.ttf"},
    "yugothic":       {"system": ["YuGothR.ttc", "YuGothB.ttc", "YuGothM.ttc"], "bundled": "ipaexg.ttf"},
    "yugothicbold":   {"system": ["YuGothB.ttc"],                      "bundled": "ipaexg.ttf"},
    "meiryo":         {"system": ["meiryo.ttc"],                       "bundled": "ipaexg.ttf"},
    "meiryoui":       {"system": ["meiryo.ttc"],                       "bundled": "ipaexg.ttf"},
    "ipaexmincho":    {"bundled": "ipaexm.ttf"},
    "ipaexgothic":    {"bundled": "ipaexg.ttf"},
    "ipamincho":      {"system": ["ipam.ttf"],                         "bundled": "ipaexm.ttf"},
    "ipagothic":      {"system": ["ipag.ttf"],                         "bundled": "ipaexg.ttf"},
    "ipapgothic":     {"system": ["ipagp.ttf"],                        "bundled": "ipaexg.ttf"},
    "notosansjp":     {"system": ["NotoSansCJKjp-Regular.otf"],        "bundled": "NotoSansCJKjp-Regular.otf"},
    "notosanscjkjp":  {"bundled": "NotoSansCJKjp-Regular.otf"},
    "notoserifjp":    {"system": ["NotoSerifCJKjp-Regular.otf"],       "bundled": "ipaexm.ttf"},
    "notoserifcjkjp": {"system": ["NotoSerifCJKjp-Regular.otf"],       "bundled": "ipaexm.ttf"},
    "timesnewroman":  {"system": ["times.ttf"],                        "bundled": "LiberationSerif-Regular.ttf"},
    "timesnewromanpsmt": {"system": ["times.ttf"],                     "bundled": "LiberationSerif-Regular.ttf"},
    "times":          {"system": ["times.ttf"],                        "bundled": "LiberationSerif-Regular.ttf"},
    "century":        {"system": ["century.ttf", "CENTURY.TTF"],       "bundled": "LiberationSerif-Regular.ttf"},
    "georgia":        {"system": ["georgia.ttf"],                      "bundled": "LiberationSerif-Regular.ttf"},
    "cambria":        {"system": ["cambria.ttc", "cambria.ttf"],       "bundled": "LiberationSerif-Regular.ttf"},
    "calibri":        {"system": ["calibri.ttf"],                      "bundled": "NotoSansCJKjp-Regular.otf"},
    "arial":          {"system": ["arial.ttf"],                        "bundled": "LiberationSans-Regular.ttf"},
    "arialmt":        {"system": ["arial.ttf"],                        "bundled": "LiberationSans-Regular.ttf"},
    "arialboldmt":    {"system": ["arialbd.ttf"],                      "bundled": "LiberationSans-Regular.ttf"},
    "helvetica":      {"system": ["arial.ttf"],                        "bundled": "LiberationSans-Regular.ttf"},
    "hiraminpro":     {"system": ["msmincho.ttc"],                     "bundled": "ipaexm.ttf"},
    "hirakakupro":    {"system": ["msgothic.ttc"],                     "bundled": "ipaexg.ttf"},
}

_SUFFIXES = ("-Regular", "-Bold", "-Italic", "-BoldItalic", "-Medium", "-Demibold",
             "-Light", "PS-BoldMT", "PS-ItalicMT", "PSMT", "MT", "UI")


def normalize(name: str) -> str:
    if not name:
        return ""
    n = GARBLED.get(name, name)
    n = n.split("+")[-1]  # subset prefix AAAAAA+Font
    for suf in _SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)]
            break
    return n.replace(" ", "").replace("\u3000", "").replace("-", "").lower()


def system_font_dirs() -> list[str]:
    dirs = []
    windir = os.environ.get("WINDIR", r"C:\Windows")
    dirs.append(os.path.join(windir, "Fonts"))
    dirs.append(os.environ.get("SUMI_FONT_DIRS", ""))
    dirs += ["/usr/share/fonts", os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if d and os.path.isdir(d)]


def find_system_file(fname: str) -> str | None:
    """Find a font file by name, case-insensitive, recursive 2 levels."""
    for base in system_font_dirs():
        cand = os.path.join(base, fname)
        if os.path.isfile(cand):
            return cand
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        low = fname.lower()
        for e in entries:
            if e.lower() == low:
                return os.path.join(base, e)
        for e in entries:  # one subdir level (linux font dirs)
            sub = os.path.join(base, e)
            if os.path.isdir(sub):
                cand = os.path.join(sub, fname)
                if os.path.isfile(cand):
                    return cand
    return None


def bundled_path(fname: str) -> str | None:
    if not FONTS_DIR:
        return None
    p = os.path.join(FONTS_DIR, fname)
    return p if os.path.isfile(p) else None


def resolve(font_name: str) -> dict:
    """Return {'path': str|None, 'source': 'system'|'bundled'|'none', 'name': normalized}"""
    key = normalize(font_name)
    ent = ALIAS.get(key)
    if ent:
        for sysname in ent.get("system", []):
            p = find_system_file(sysname)
            if p:
                return {"path": p, "source": "system", "name": key}
        p = bundled_path(ent.get("bundled", ""))
        if p:
            return {"path": p, "source": "bundled", "name": key}
    # direct: name itself is a file
    if os.path.isfile(font_name):
        return {"path": font_name, "source": "file", "name": key}
    p = find_system_file(font_name)
    if p:
        return {"path": p, "source": "system", "name": key}
    return {"path": None, "source": "none", "name": key}


def default_font(kind: str = "gothic") -> str:
    """Bundled fallback font path. kind: gothic|mincho|serif|sans"""
    table = {
        "gothic": "ipaexg.ttf",
        "mincho": "ipaexm.ttf",
        "serif": "LiberationSerif-Regular.ttf",
        "sans": "LiberationSans-Regular.ttf",
        "jp_sans": "NotoSansCJKjp-Regular.otf",
    }
    p = bundled_path(table.get(kind, "ipaexg.ttf"))
    if p:
        return p
    raise FileNotFoundError("bundled fonts missing; run scripts/sync_fonts.sh")


def inventory() -> dict:
    out = {"fonts_dir": FONTS_DIR, "bundled": {}, "system_resolved": {}}
    if FONTS_DIR and os.path.isdir(FONTS_DIR):
        for f in sorted(os.listdir(FONTS_DIR)):
            if f.lower().endswith((".ttf", ".otf", ".ttc")):
                out["bundled"][f] = os.path.getsize(os.path.join(FONTS_DIR, f))
    for k in ALIAS:
        r = resolve(k)
        if r["path"]:
            out["system_resolved"][k] = {"path": r["path"], "source": r["source"]}
    return out
