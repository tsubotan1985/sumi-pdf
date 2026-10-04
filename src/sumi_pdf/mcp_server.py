"""Sumi PDF MCP server - dependency-free stdio JSON-RPC (MCP protocol).

Register with a client, e.g. Hermes:
  hermes mcp add sumi-pdf -- python -m sumi_pdf.mcp_server
(env PYTHONPATH must contain src/, or pip install -e . first)

Tools: inspect_fonts / redact_pdf / replace_text / extract_text / ocr_pdf_page
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
    {"name": "inspect_fonts", "description": "List embedded fonts of a PDF and Sumi fallbacks.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"]}},
    {"name": "redact_pdf",
     "description": "True-redact rectangles (text data + image pixels removed). "
                    "rects: [[x0,y0,x1,y1],...] PDF points; saves to out_path (default overwrite).",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"},
                                    "rects": {"type": "array", "items": {"type": "array",
                                                                          "items": {"type": "number"}}},
                                    "out_path": {"type": "string"}, "match_bg": {"type": "boolean"}},
                     "required": ["path", "page", "rects"]}},
    {"name": "replace_text",
     "description": "Replace text in PDF data (not a black box) at the same baseline. "
                    "font: alias like ms-mincho / ipaexgothic / or a file path.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"},
                                    "find": {"type": "string"}, "replace": {"type": "string"},
                                    "out_path": {"type": "string"}, "font": {"type": "string"}},
                     "required": ["path", "page", "find", "replace"]}},
    {"name": "extract_text", "description": "Plain text of one page.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"}},
                     "required": ["path", "page"]}},
    {"name": "ocr_pdf_page", "description": "OCR one page locally (tesseract / NDLOCR-Lite).",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}, "page": {"type": "integer"}},
                     "required": ["path", "page"]}},
]


def _text_result(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": False}


def handle(method: str, params: dict, pid) -> str | None:
    if method == "initialize":
        return _ok(pid, {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "sumi-pdf", "version": "0.1.0"}})
    if method == "tools/list":
        return _ok(pid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        a = params.get("arguments", {})
        try:
            from . import fonts as F  # noqa: F401 (validates package context)
            from . import redact as R, textedit as T
            import pymupdf as fitz
            from .ocr import ocr_page
            if name == "inspect_fonts":
                doc = fitz.open(a["path"])
                try:
                    fonts = T.doc_fonts(doc)
                finally:
                    doc.close()
                for f in fonts:
                    r = F.resolve(f["name"])
                    f["fallback"] = {"path": r["path"], "source": r["source"]}
                return _ok(pid, _text_result(json.dumps(fonts, ensure_ascii=False, indent=1)))
            if name == "redact_pdf":
                doc = fitz.open(a["path"])
                try:
                    infos = R.redact(doc[a["page"]], a["rects"],
                                     match_bg=a.get("match_bg", True))
                    doc.save(a.get("out_path") or a["path"], garbage=4, deflate=True)
                finally:
                    doc.close()
                return _ok(pid, _text_result(json.dumps(
                    {"applied": infos, "saved": a.get("out_path") or a["path"]},
                    ensure_ascii=False)))
            if name == "replace_text":
                doc = fitz.open(a["path"])
                try:
                    r = T.replace_text(doc, a["page"], a["find"], a["replace"],
                                       a.get("font") or None)
                    try:
                        doc.subset_fonts()
                    except Exception:
                        pass
                    doc.save(a.get("out_path") or a["path"], garbage=4, deflate=True)
                finally:
                    doc.close()
                return _ok(pid, _text_result(json.dumps(r, ensure_ascii=False)))
            if name == "extract_text":
                doc = fitz.open(a["path"])
                try:
                    return _ok(pid, _text_result(doc[a["page"]].get_text()))
                finally:
                    doc.close()
            if name == "ocr_pdf_page":
                return _ok(pid, _text_result(json.dumps(ocr_page(a["path"], a["page"]),
                                                         ensure_ascii=False)))
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
