"""Sumi PDF MCP server - dependency-free stdio JSON-RPC (MCP protocol). MIT engine.

Register with a client, e.g. Hermes:
  hermes mcp add sumi-pdf -- python -m sumi_pdf.mcp_server
(env PYTHONPATH must contain src/, or pip install -e . first)

Tools: inspect_fonts / redact_pdf / replace_text / extract_text / ocr_pdf_page /
       encrypt_pdf / decrypt_pdf / pdf_permissions
"""
from __future__ import annotations

import json
import os
import sys

PROTOCOL = "2024-11-05"


def _ok(pid, result):
    return json.dumps({"jsonrpc": "2.0", "id": pid, "result": result}, ensure_ascii=False)


def _err(pid, code, message):
    return json.dumps({"jsonrpc": "2.0", "id": pid,
                       "error": {"code": code, "message": message}}, ensure_ascii=False)


TOOLS = [
    {"name": "inspect_fonts", "description": "List fonts of a PDF and Sumi fallbacks.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]}},
    {"name": "redact_pdf",
     "description": "True-redact rectangles (text data removed + rect painted). "
                    "rects: [[x0,y0,x1,y1],...] PDF points; saves to out_path (default overwrite).",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"},
                                    "rects": {"type": "array", "items": {"type": "array",
                                                                          "items": {"type": "number"}}},
                                    "out_path": {"type": "string"}, "match_bg": {"type": "boolean"}},
                     "required": ["path", "page", "rects"]}},
    {"name": "replace_text",
     "description": "Replace text in PDF data (not a black box) at the same baseline. "
                    "Uses bundled IPAex Gothic for reinsertion.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"},
                                    "find": {"type": "string"}, "replace": {"type": "string"},
                                    "out_path": {"type": "string"}, "size": {"type": "number"}},
                     "required": ["path", "page", "find", "replace"]}},
    {"name": "extract_text", "description": "Plain text of one page.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"}},
                     "required": ["path", "page"]}},
    {"name": "ocr_pdf_page", "description": "OCR one page locally (tesseract / NDLOCR-Lite).",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"},
                                    "lang": {"type": "string", "enum": ["auto", "jpn", "jpn_vert", "ndl"]}},
                     "required": ["path", "page"]}},
    {"name": "encrypt_pdf",
     "description": "Set AES-256 password + permissions. flags: print_,copy,modify,annotate,forms,assemble,print_high",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "out_path": {"type": "string"},
                                    "user_pw": {"type": "string"}, "owner_pw": {"type": "string"},
                                    "algorithm": {"type": "string", "enum": ["AES-256", "AES-128", "RC4-128"]},
                                    "allow": {"type": "object"}},
                     "required": ["path", "user_pw"]}},
    {"name": "decrypt_pdf",
     "description": "Remove PDF encryption with the open/owner password.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "out_path": {"type": "string"},
                                    "password": {"type": "string"}},
                     "required": ["path", "password"]}},
    {"name": "pdf_permissions",
     "description": "Encrypted? and effective permission flags of a PDF.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "password": {"type": "string"}},
                     "required": ["path"]}},
    {"name": "img_redact_pdf",
     "description": "Destructively blank pixels inside rects in the page's embedded images (true data removal).",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"},
                                    "rects": {"type": "array", "items": {"type": "array",
                                                                          "items": {"type": "number"}}},
                                    "out_path": {"type": "string"}, "fill_rgb": {"type": "array"}},
                     "required": ["path", "page", "rects"]}},
    {"name": "compress_pdf",
     "description": "Recompress images and streams; smaller file. image_quality 1-100.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "out_path": {"type": "string"},
                                    "image_quality": {"type": "integer"}, "grayscale": {"type": "boolean"}},
                     "required": ["path"]}},
    {"name": "watermark_pdf",
     "description": "Overlay diagonal text watermark on a page (saved on out_path).",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "out_path": {"type": "string"},
                                    "page": {"type": "integer"}, "text": {"type": "string"},
                                    "opacity": {"type": "number"}, "angle": {"type": "integer"}},
                     "required": ["path", "text"]}},
    {"name": "page_numbers_pdf",
     "description": "Stamp page numbers on all pages (fmt like '{n} / {total}').",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "out_path": {"type": "string"},
                                    "fmt": {"type": "string"}, "pos": {"type": "string"},
                                    "start": {"type": "integer"}},
                     "required": ["path"]}},
    {"name": "get_metadata",
     "description": "Read document info dictionary.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "set_metadata",
     "description": "Set Title/Author/Subject/Keywords/Creator/Producer; value null deletes.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "out_path": {"type": "string"},
                                    "fields": {"type": "object"}},
                     "required": ["path", "fields"]}},
    {"name": "sanitize_pdf",
     "description": "Strip JavaScript / embedded files / metadata (/ links / annotations) to a cleaned copy.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "out_path": {"type": "string"},
                                    "remove_js": {"type": "boolean"},
                                    "remove_embedded_files": {"type": "boolean"},
                                    "remove_metadata": {"type": "boolean"},
                                    "remove_links": {"type": "boolean"},
                                    "remove_annotations": {"type": "boolean"}},
                     "required": ["path"]}},
]


def _text_result(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": False}


def _read(p: str) -> bytes:
    with open(p, "rb") as fh:
        return fh.read()


def _save(data: bytes, out: str) -> str:
    from . import edit as E
    if not out:
        raise ValueError("out_path required for in-place edit safety" )
    E.save_optimized(data, out)
    return out


def handle(method: str, params: dict, pid) -> str | None:
    if method == "initialize":
        return _ok(pid, {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "sumi-pdf", "version": "0.5.0"}})
    if method == "tools/list":
        return _ok(pid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        a = params.get("arguments", {})
        try:
            from . import edit as E, pdfio as P
            from . import redact as R, textedit as T
            from . import stirling as ST
            from .ocr import ocr_page
            if name == "inspect_fonts":
                fonts = P.font_inventory(_read(a["path"]))
                from . import fonts as F
                for f in fonts:
                    r = F.resolve(f["name"])
                    f["fallback"] = {"path": r["path"], "source": r["source"]}
                return _ok(pid, _text_result(json.dumps(fonts, ensure_ascii=False, indent=1)))
            if name == "redact_pdf":
                out = R.redact(_read(a["path"]), a["page"], [tuple(r) for r in a["rects"]],
                               match_bg=a.get("match_bg", True))
                saved = _save(out["bytes"], a.get("out_path") or a["path"])
                return _ok(pid, _text_result(json.dumps(
                    {"applied": {"dropped": out["dropped"], "filled": out["filled"]},
                     "saved": saved}, ensure_ascii=False)))
            if name == "replace_text":
                r = T.replace(_read(a["path"]), a["page"], a["find"], a["replace"],
                              a.get("size"))
                saved = _save(r["bytes"], a.get("out_path") or a["path"])
                return _ok(pid, _text_result(json.dumps(
                    {"replaced": r["replaced"], "saved": saved}, ensure_ascii=False)))
            if name == "extract_text":
                return _ok(pid, _text_result(P.extract_text(_read(a["path"]), a["page"])))
            if name == "ocr_pdf_page":
                return _ok(pid, _text_result(json.dumps(
                    ocr_page(a["path"], a["page"], lang=a.get("lang", "auto")),
                    ensure_ascii=False)))
            if name == "encrypt_pdf":
                from . import crypto as C
                allow = a.get("allow") or {}
                kw = {k: bool(allow.get(k, True)) for k in
                      ("print_", "copy", "modify", "annotate", "forms", "assemble", "print_high")}
                r = C.encrypt_pdf(a["path"], a.get("out_path") or
                                  os.path.splitext(a["path"])[0] + "-enc.pdf",
                                  a.get("user_pw", ""), a.get("owner_pw") or None,
                                  a.get("algorithm", "AES-256"), **kw)
                return _ok(pid, _text_result(json.dumps(r, ensure_ascii=False)))
            if name == "decrypt_pdf":
                from . import crypto as C
                r = C.decrypt_pdf(a["path"], a.get("out_path") or
                                  os.path.splitext(a["path"])[0] + "-dec.pdf",
                                  a.get("password", ""))
                return _ok(pid, _text_result(json.dumps(r, ensure_ascii=False)))
            if name == "pdf_permissions":
                from . import crypto as C
                return _ok(pid, _text_result(json.dumps(
                    C.inspect(a["path"], a.get("password", "")), ensure_ascii=False)))
            if name == "img_redact_pdf":
                from . import imgredact as IR
                r = IR.remove_pixels(_read(a["path"]), a["page"],
                                     [tuple(x) for x in a["rects"]],
                                     fill=tuple(a.get("fill_rgb") or [255, 255, 255]))
                saved = _save(r["bytes"], a.get("out_path") or a["path"]) if r.get("bytes") else a["path"]
                return _ok(pid, _text_result(json.dumps(
                    {"edited": r["edited"], "saved": saved}, ensure_ascii=False)))
            if name == "compress_pdf":
                r = ST.compress(_read(a["path"]), a.get("out_path") or
                                os.path.splitext(a["path"])[0] + "-c.pdf",
                                image_quality=int(a.get("image_quality", 70)),
                                grayscale=bool(a.get("grayscale", False)))
                return _ok(pid, _text_result(json.dumps(r, ensure_ascii=False)))
            if name == "watermark_pdf":
                out = a.get("out_path") or a["path"]
                d2 = ST.watermark(_read(a["path"]), int(a.get("page", 0)),
                                  a["text"], opacity=float(a.get("opacity", 0.15)),
                                  angle=int(a.get("angle", 45)))
                saved = _save(d2, out)
                return _ok(pid, _text_result(json.dumps({"saved": saved}, ensure_ascii=False)))
            if name == "page_numbers_pdf":
                r = ST.page_numbers(_read(a["path"]), a.get("out_path") or
                                    os.path.splitext(a["path"])[0] + "-num.pdf",
                                    fmt=a.get("fmt", "{n} / {total}"),
                                    pos=a.get("pos", "bottom-center"),
                                    start=int(a.get("start", 1)))
                return _ok(pid, _text_result(json.dumps(r, ensure_ascii=False)))
            if name == "get_metadata":
                return _ok(pid, _text_result(json.dumps(
                    ST.get_metadata(_read(a["path"])), ensure_ascii=False)))
            if name == "set_metadata":
                r = ST.set_metadata(_read(a["path"]), a.get("out_path") or
                                    os.path.splitext(a["path"])[0] + "-meta.pdf",
                                    **(a.get("fields") or {}))
                return _ok(pid, _text_result(json.dumps(r, ensure_ascii=False)))
            if name == "sanitize_pdf":
                r = ST.sanitize(_read(a["path"]), a.get("out_path") or
                                os.path.splitext(a["path"])[0] + "-clean.pdf",
                                remove_js=bool(a.get("remove_js", True)),
                                remove_embedded_files=bool(a.get("remove_embedded_files", True)),
                                remove_metadata=bool(a.get("remove_metadata", True)),
                                remove_links=bool(a.get("remove_links", False)),
                                remove_annotations=bool(a.get("remove_annotations", False)))
                return _ok(pid, _text_result(json.dumps(r, ensure_ascii=False)))
            return _err(pid, -32601, f"unknown tool: {name}")
        except Exception as e:  # tool error -> isError result
            return _ok(pid, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                             "isError": True})
    if method.startswith("notifications/"):
        return None
    return _err(pid, -32601, f"unknown method: {method}" if pid is not None else method)


def main():
    try:  # Windows console defaults to cp932; MCP JSON must be UTF-8
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        pid = msg.get("id")
        out = handle(msg.get("method", ""), msg.get("params", {}) or {}, pid)
        if out is not None:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
