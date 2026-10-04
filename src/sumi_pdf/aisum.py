"""AI summarization via a user-supplied OpenAI-compatible endpoint (BYO).

The API key and endpoint live OUTSIDE the repo (never committed):
- Windows frozen exe: ``%LOCALAPPDATA%/sumi-pdf/ai-config.json``
- development:        ``~/.config/sumi-pdf/ai-config.json``
Override for tests with ``SUMI_AI_CONFIG``.

HTTP calls use stdlib ``urllib`` only (no new dependencies). Long texts are
summarized map-reduce style: per-chunk summaries -> one final merge. Requests
to loopback endpoints bypass any system proxy so a local LLM (e.g. :8888)
keeps working even with http_proxy set.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import pdfio as P

CHUNK_SIZE = 6000        # chars per chunk
MAX_CHUNKS = 40          # > this -> ValueError (too long for map-reduce)
DEFAULT_TIMEOUT = 60.0   # seconds per HTTP request

DEFAULT_PROMPT = "次のテキストを、固有名詞・数値を保ちながら日本語で簡潔に要約してください。"
CHUNK_PROMPT = "これは長い文書の部分{i}/{n}です。この部分の内容を要約してください（後で全体を統合します）。"
REDUCE_PROMPT = "以下は1つの文書を{n}部分に分けて要約したものです。重複を除き、全体を1つの要約に統合してください。"

_DEFAULTS = {"base_url": "", "api_key": "", "model": "", "timeout": DEFAULT_TIMEOUT}


# ---------------------------------------------------------------- config

def config_path() -> str:
    """User config file location. Env override wins; frozen exe uses %LOCALAPPDATA%."""
    env = os.environ.get("SUMI_AI_CONFIG")
    if env:
        return env
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "sumi-pdf", "ai-config.json")
    return os.path.join(os.path.expanduser("~"), ".config", "sumi-pdf", "ai-config.json")


def load_config() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        for k in _DEFAULTS:
            if data.get(k) is not None:
                cfg[k] = data[k]
    try:
        cfg["timeout"] = max(1.0, float(cfg["timeout"]))
    except (TypeError, ValueError):
        cfg["timeout"] = DEFAULT_TIMEOUT
    return cfg


def save_config(cfg: dict) -> str:
    """Persist config atomically; returns the path written."""
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {k: cfg.get(k, _DEFAULTS[k]) for k in _DEFAULTS}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def masked_config(cfg: dict | None = None) -> dict:
    """Config for display: only the last 4 chars of the key are visible."""
    cfg = cfg if cfg is not None else load_config()
    key = str(cfg.get("api_key") or "")
    masked = ("****" + key[-4:]) if len(key) > 4 else ("****" if key else "")
    return {"base_url": cfg.get("base_url", ""), "model": cfg.get("model", ""),
            "timeout": cfg.get("timeout", DEFAULT_TIMEOUT),
            "api_key": masked, "has_key": bool(key)}


# ---------------------------------------------------------------- chunking

def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Fixed-size character chunks; concatenation restores the original text."""
    text = text or ""
    size = max(1, int(size))
    return [text[i:i + size] for i in range(0, len(text), size)]


def parse_pages(spec: str, n_pages: int) -> list[int]:
    """'2-5' / '1,3' / '8-' (1-based, inclusive) -> sorted 0-based pnos."""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("ページ範囲が空です")
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d*))?", part)
        if not m:
            raise ValueError(f"ページ範囲が不正です: {part}")
        a = int(m.group(1))
        if m.group(2) is None:     # no dash: single page
            b = a
        elif m.group(2):
            b = int(m.group(2))
        else:                      # "8-" -> to the last page
            b = n_pages
        if not (1 <= a <= b <= n_pages):
            raise ValueError(f"ページ範囲が範囲外です: {part}（全{n_pages}ページ）")
        out.update(range(a - 1, b))
    if not out:
        raise ValueError("ページ範囲が空です")
    return sorted(out)


# ---------------------------------------------------------------- HTTP

def _opener_for(url: str):
    """Never send loopback traffic through a system proxy (local LLMs on :8888 etc.)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _chat(config: dict, messages: list[dict]) -> str:
    """One POST to {base_url}/chat/completions -> choices[0].message.content."""
    base = str(config.get("base_url") or "").strip().rstrip("/")
    model = str(config.get("model") or "").strip()
    if not base:
        raise ValueError("base_url が未設定です")
    if not model:
        raise ValueError("model が未設定です")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = str(config.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    timeout = max(1.0, float(config.get("timeout") or DEFAULT_TIMEOUT))
    try:
        with _opener_for(url).open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(300).decode("utf-8", "replace")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail or e.reason}") from None
    except TimeoutError:
        raise RuntimeError(f"タイムアウト（{timeout:g}秒）") from None
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), TimeoutError):
            raise RuntimeError(f"タイムアウト（{timeout:g}秒）") from None
        raise RuntimeError(f"接続できません: {getattr(e, 'reason', e)}") from None
    except OSError as e:
        raise RuntimeError(f"接続できません: {e}") from None
    try:
        content = json.loads(raw.decode("utf-8", "replace"))["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("応答形式が不正です（choices[0].message.content がありません）") from None
    if not isinstance(content, str):
        raise RuntimeError("応答形式が不正です（content が文字列ではありません）")
    return content


# ---------------------------------------------------------------- summarize

def summarize(text: str, config: dict, prompt: str | None = None,
              size: int | None = None, max_chunks: int | None = None) -> dict:
    """Summarize text; long input goes map-reduce (per-chunk -> merge).

    Returns {"summary": str, "chunks": int}. Raises ValueError for config /
    length problems, RuntimeError for HTTP failures.
    """
    size = CHUNK_SIZE if size is None else size
    max_chunks = MAX_CHUNKS if max_chunks is None else max_chunks
    text = text or ""
    if not text.strip():
        raise ValueError("要約するテキストがありません")
    chunks = chunk_text(text, size)
    if len(chunks) > max_chunks:
        raise ValueError(
            f"テキストが長すぎます（{len(chunks)}チャンク > 上限{max_chunks}）。"
            "スコープやページ範囲を絞ってください")
    extra = (prompt or "").strip()
    if len(chunks) == 1:
        instr = (extra + "\n\n") if extra else ""
        summary = _chat(config, [{"role": "user", "content": instr + DEFAULT_PROMPT + "\n\n" + text}])
        return {"summary": summary.strip(), "chunks": 1}
    parts = []
    for i, ch in enumerate(chunks):
        msg = CHUNK_PROMPT.format(i=i + 1, n=len(chunks)) + "\n\n" + ch
        parts.append(_chat(config, [{"role": "user", "content": msg}]).strip())
    joined = "\n\n".join(f"【部分{i + 1}】{p}" for i, p in enumerate(parts))
    instr = (extra + "\n\n") if extra else ""
    final = _chat(config, [{"role": "user", "content": instr + REDUCE_PROMPT.format(n=len(chunks)) + "\n\n" + joined}])
    return {"summary": final.strip(), "chunks": len(chunks)}


# ---------------------------------------------------------------- rect text

def extract_in_rects(data: bytes, pno: int, rects: list) -> str:
    """Text of the chars whose glyph box overlaps any rect (PDF pt, y-up).

    Same text source as /api/extract (pdfium textpage); ``\\r\\n`` runs are
    kept as line breaks when the preceding char is inside the rect.
    """
    boxes = []
    for r in (rects or []):
        x0, x1 = min(r[0], r[2]), max(r[0], r[2])
        y0, y1 = min(r[1], r[3]), max(r[1], r[3])
        if x1 > x0 and y1 > y0:
            boxes.append((x0, y0, x1, y1))
    if not boxes:
        return ""
    d = P._doc(data)
    try:
        tp = d[pno].get_textpage()
        n = tp.count_chars()
        pieces: list[str] = []
        prev_kept = False
        for i in range(n):
            u = _R_get_unicode(tp, i)
            if u and u > 0 and u in (0x0D, 0x0A):        # \r \n terminate the line
                if prev_kept and (not pieces or pieces[-1] != "\n"):
                    pieces.append("\n")
                continue
            if not u or u <= 0:
                prev_kept = False
                continue
            try:
                b = tp.get_charbox(i)
            except Exception:
                b = None
            if not isinstance(b, tuple) or len(b) != 4:
                prev_kept = False
                continue
            x0, y0, x1, y1 = b
            if any(x0 < r[2] and x1 > r[0] and y0 < r[3] and y1 > r[1] for r in boxes):
                pieces.append(chr(u))
                prev_kept = True
            else:
                prev_kept = False
        text = "".join(pieces)
        return text.replace("\xa0", " ")
    finally:
        d.close()


def _R_get_unicode(tp, index: int) -> int:
    """FPDFText_GetUnicode via pdfio's raw binding (kept local: no pdfio edits)."""
    return P._R.FPDFText_GetUnicode(tp.raw, index)
