"""Task 10: AI要約（BYO OpenAI互換エンドポイント）のテスト。ネットワーク不要。

- 設定の保存/読込（repo外のユーザー設定dir、SUMI_AI_CONFIGで差し替え）
- マスク応答（key末尾4桁のみ）
- チャンク分割 / チャンク上限
- モックHTTPサーバ（http.serverをpytest内で起動）での要約呼出 / map-reduce /
  エンドポイントエラー / タイムアウト / 接続拒否
- rect内テキスト抽出（PDF pt、y上がり）
- server.py エンドポイント3件の結線
"""
from __future__ import annotations

import contextlib
import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from sumi_pdf import aisum as A
from sumi_pdf import server as sv


# ---------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Config path -> tmp dir (never the real ~/.config, never the repo)."""
    monkeypatch.setenv("SUMI_AI_CONFIG", str(tmp_path / "ai-config.json"))
    old_S = dict(sv.S)
    try:
        yield
    finally:
        sv.S.clear()
        sv.S.update(old_S)


def _pdf(text_lines: list[str] | None = None, pages: int = 1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 400))
    for p in range(pages):
        for i, line in enumerate(text_lines or []):
            c.drawString(30, 330 - 20 * i, line)
        c.showPage()
    c.save()
    return buf.getvalue()


@contextlib.contextmanager
def mock_ai_endpoint(fn):
    """fn(headers: dict, req: dict) -> (status:int, body:dict); reqs list recorded."""
    reqs: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                req = {"path": self.path, "body": json.loads(raw or b"{}"),
                       "auth": self.headers.get("Authorization")}
            except ValueError:
                req = {"path": self.path, "body": None, "auth": self.headers.get("Authorization")}
            reqs.append(req)
            status, obj = fn(self, req)
            payload = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    class Srv(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, *a):  # client timeouts land here; keep output clean
            pass

    srv = Srv(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, reqs
    finally:
        srv.shutdown()
        srv.server_close()


def _openai_body(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _cfg(port: int, **over) -> dict:
    cfg = {"base_url": f"http://127.0.0.1:{port}", "api_key": "sk-test-1234abcd",
           "model": "test-model", "timeout": 10.0}
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------- config

def test_save_then_load_roundtrip(tmp_path):
    path = A.save_config({"base_url": "http://127.0.0.1:8888/v1", "api_key": "sk-abc",
                          "model": "m1", "timeout": 30.0})
    assert path == A.config_path()
    assert path.endswith("ai-config.json")
    assert str(tmp_path) in path                    # repo外（SUMI_AI_CONFIG差し替え先）
    cfg = A.load_config()
    assert cfg["base_url"] == "http://127.0.0.1:8888/v1"
    assert cfg["api_key"] == "sk-abc"
    assert cfg["model"] == "m1"
    assert cfg["timeout"] == 30.0


def test_save_config_creates_missing_dirs():
    path = A.save_config({"base_url": "x", "model": "m"})
    import os
    assert os.path.isfile(path)


def test_load_config_missing_file_returns_defaults():
    cfg = A.load_config()
    assert cfg == {"base_url": "", "api_key": "", "model": "", "timeout": A.DEFAULT_TIMEOUT}


def test_load_config_corrupt_json_returns_defaults():
    import os
    os.makedirs(os.path.dirname(A.config_path()), exist_ok=True)
    with open(A.config_path(), "w", encoding="utf-8") as fh:
        fh.write("{ not json !!")
    assert A.load_config()["model"] == ""
    assert A.load_config()["timeout"] == A.DEFAULT_TIMEOUT


def test_config_path_frozen_uses_localappdata(monkeypatch):
    monkeypatch.delenv("SUMI_AI_CONFIG", raising=False)
    monkeypatch.setattr(__import__("sys"), "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Youichi\AppData\Local")
    import os
    p = A.config_path()
    norm = os.path.normpath(p)
    assert norm.endswith(os.path.join("sumi-pdf", "ai-config.json"))
    assert "AppData" in norm or "Local" in norm


def test_config_path_env_overrides_frozen(monkeypatch):
    monkeypatch.setenv("SUMI_AI_CONFIG", "/tmp/explicit.json")
    monkeypatch.setattr(__import__("sys"), "frozen", True, raising=False)
    assert A.config_path() == "/tmp/explicit.json"


def test_masked_config_shows_only_last4():
    m = A.masked_config({"base_url": "http://x/v1", "api_key": "sk-test-1234abcd",
                         "model": "m", "timeout": 5})
    assert m["api_key"].endswith("abcd")
    assert m["api_key"].startswith("****")
    assert m["has_key"] is True
    assert "sk-test-1234abcd" not in json.dumps(m)   # full key never leaks


def test_masked_config_empty_key():
    m = A.masked_config({"base_url": "", "api_key": "", "model": "", "timeout": 60})
    assert m["api_key"] == ""
    assert m["has_key"] is False


def test_masked_config_short_key_fully_masked():
    m = A.masked_config({"api_key": "abc"})
    assert m["api_key"] == "****"


# ---------------------------------------------------------------- chunking

def test_chunk_text_default_size():
    text = "あ" * 13000
    chunks = A.chunk_text(text)
    assert all(len(c) <= 6000 for c in chunks)
    assert "".join(chunks) == text
    assert len(chunks) == 3


def test_chunk_text_custom_size_and_empty():
    assert A.chunk_text("abc", size=2) == ["ab", "c"]
    assert A.chunk_text("") == []


# ---------------------------------------------------------------- summarize

def test_summarize_single_chunk_calls_endpoint():
    def handler(h, req):
        assert req["path"].endswith("/chat/completions")
        assert req["auth"] == "Bearer sk-test-1234abcd"
        assert req["body"]["model"] == "test-model"
        assert req["body"]["messages"][0]["role"] == "user"
        return 200, _openai_body("要約です")

    with mock_ai_endpoint(handler) as (srv, reqs):
        out = A.summarize("短いテキスト", _cfg(srv.server_address[1]))
    assert out == {"summary": "要約です", "chunks": 1}
    assert len(reqs) == 1


def test_summarize_map_reduce_two_chunks():
    def handler(h, req):
        i = len(reqs)                    # 1-based call number
        if i <= 3:
            return 200, _openai_body(f"S{i}")
        return 200, _openai_body("統合要約")

    text = "あ" * 12500  # 6000 + 6000 + 500
    with mock_ai_endpoint(handler) as (srv, reqs):
        out = A.summarize(text, _cfg(srv.server_address[1]))
    assert out["summary"] == "統合要約"
    assert out["chunks"] == 3
    assert len(reqs) == 4                            # 3 map + 1 reduce
    final_msg = reqs[-1]["body"]["messages"][0]["content"]
    assert "S1" in final_msg and "S2" in final_msg and "S3" in final_msg


def test_summarize_prompt_reaches_endpoint():
    def handler(h, req):
        return 200, _openai_body("ok")

    with mock_ai_endpoint(handler) as (srv, reqs):
        A.summarize("テキスト", _cfg(srv.server_address[1]), prompt="箇条書きで")
    assert "箇条書きで" in reqs[0]["body"]["messages"][0]["content"]


def test_summarize_over_chunk_limit_raises(monkeypatch):
    monkeypatch.setattr(A, "MAX_CHUNKS", 2)
    with pytest.raises(ValueError) as e:
        A.summarize("あ" * 13000, {"base_url": "http://127.0.0.1:1", "model": "m"})
    assert "チャンク" in str(e.value)


def test_summarize_requires_base_url_and_model():
    with pytest.raises(ValueError):
        A.summarize("x", {"model": "m"})
    with pytest.raises(ValueError):
        A.summarize("x", {"base_url": "http://x"})


def test_summarize_empty_text_raises():
    with pytest.raises(ValueError):
        A.summarize("   ", {"base_url": "http://x", "model": "m"})


def test_endpoint_http_500_raises_runtimeerror():
    def handler(h, req):
        return 500, {"error": {"message": "boom"}}

    with mock_ai_endpoint(handler) as (srv, _):
        with pytest.raises(RuntimeError) as e:
            A.summarize("x", _cfg(srv.server_address[1]))
    assert "500" in str(e.value)


def test_endpoint_invalid_json_response():
    def handler(h, req):
        return 200, {"unexpected": "shape"}

    with mock_ai_endpoint(handler) as (srv, _):
        with pytest.raises(RuntimeError) as e:
            A.summarize("x", _cfg(srv.server_address[1]))
    assert "応答形式" in str(e.value)


def test_endpoint_timeout():
    import time

    def handler(h, req):
        time.sleep(2.0)
        return 200, _openai_body("late")

    with mock_ai_endpoint(handler) as (srv, _):
        with pytest.raises(RuntimeError) as e:
            A.summarize("x", _cfg(srv.server_address[1], timeout=0.3))
    assert "タイムアウト" in str(e.value)


def test_endpoint_connection_refused():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(RuntimeError) as e:
        A.summarize("x", _cfg(port))
    assert "接続" in str(e.value)


# ---------------------------------------------------------------- page range

def test_parse_pages():
    assert A.parse_pages("3", 10) == [2]
    assert A.parse_pages("2-4", 10) == [1, 2, 3]
    assert A.parse_pages("1,3", 10) == [0, 2]
    assert A.parse_pages("2-2", 10) == [1]
    assert A.parse_pages("8-", 10) == [7, 8, 9]
    assert A.parse_pages(" 2 , 4 ", 10) == [1, 3]


@pytest.mark.parametrize("bad", ["", "abc", "0", "11", "5-2"])
def test_parse_pages_invalid(bad):
    with pytest.raises(ValueError):
        A.parse_pages(bad, 10)


# ---------------------------------------------------------------- rect extract

def test_extract_in_rects_one_line():
    data = _pdf(["Line One here", "Line Two here"])
    t = A.extract_in_rects(data, 0, [(25, 320, 120, 345)])   # line 1 only (PDF pt, y-up)
    assert "Line One here" in t
    assert "Line Two here" not in t
    assert "\n" in t or " " in t


def test_extract_in_rects_full_page():
    data = _pdf(["Line One here", "Line Two here"])
    t = A.extract_in_rects(data, 0, [(0, 0, 300, 400)])
    assert "Line One here" in t and "Line Two here" in t


def test_extract_in_rects_empty_rects():
    data = _pdf(["Line One here"])
    assert A.extract_in_rects(data, 0, []) == ""


# ---------------------------------------------------------------- server API

def _client() -> TestClient:
    return TestClient(sv.app)


def test_ai_config_get_masks_key():
    A.save_config({"base_url": "http://127.0.0.1:8888/v1", "api_key": "sk-secret-9999",
                   "model": "m", "timeout": 60.0})
    r = _client().get("/api/ai/config")
    assert r.status_code == 200
    j = r.json()
    assert j["api_key"].endswith("9999")
    assert "sk-secret-9999" not in json.dumps(j)
    assert j["base_url"] == "http://127.0.0.1:8888/v1"


def test_ai_config_put_saves_and_keeps_key_when_blank():
    c = _client()
    r = c.put("/api/ai/config", json={"base_url": "http://x/v1", "api_key": "sk-abc-1234",
                                      "model": "m1", "timeout": 30})
    assert r.status_code == 200 and r.json()["saved"] is True
    # blank api_key keeps the stored one (masked round-trip must not clobber it)
    r2 = c.put("/api/ai/config", json={"base_url": "http://y/v1", "api_key": "", "model": "m2"})
    assert r2.status_code == 200
    assert A.load_config()["api_key"] == "sk-abc-1234"
    assert A.load_config()["base_url"] == "http://y/v1"
    assert A.load_config()["model"] == "m2"


def test_ai_summarize_uses_override_text(monkeypatch):
    seen = {}

    def fake_summarize(text, cfg, prompt=None):
        seen["text"], seen["prompt"] = text, prompt
        return {"summary": "S", "chunks": 1}

    monkeypatch.setattr(sv.A, "summarize", fake_summarize)
    A.save_config({"base_url": "http://x", "api_key": "k", "model": "m"})
    r = _client().post("/api/ai/summarize", json={"scope": "all", "text": "直接テキスト",
                                                  "prompt": "箇条書きで"})
    assert r.status_code == 200
    assert r.json() == {"summary": "S", "chunks": 1}
    assert seen == {"text": "直接テキスト", "prompt": "箇条書きで"}


def test_ai_summarize_scope_page_and_range(monkeypatch):
    p = io.BytesIO()
    c = canvas.Canvas(p, pagesize=(300, 400))
    for i in range(3):
        c.drawString(30, 330, f"page{i}")
        c.showPage()
    c.save()
    data = p.getvalue()
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    try:
        sv.open_pdf(sv.OpenReq(path=path))

        seen = {}

        def fake_summarize(text, cfg, prompt=None):
            seen["text"] = text
            return {"summary": "S", "chunks": 1}

        monkeypatch.setattr(sv.A, "summarize", fake_summarize)
        A.save_config({"base_url": "http://x", "model": "m"})

        r = _client().post("/api/ai/summarize", json={"scope": "page", "page": 1})
        assert r.status_code == 200
        assert seen["text"] == "page1"

        r = _client().post("/api/ai/summarize", json={"scope": "range", "range": "1,3"})
        assert r.status_code == 200
        assert seen["text"] == "page0\n\npage2"
    finally:
        os.unlink(path)


def test_ai_summarize_selection_extracts_rect_text(monkeypatch):
    fd_path = None
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    fd_path = path
    with os.fdopen(fd, "wb") as fh:
        fh.write(_pdf(["Line One here", "Line Two here"]))
    try:
        sv.open_pdf(sv.OpenReq(path=path))
        seen = {}

        def fake_summarize(text, cfg, prompt=None):
            seen["text"] = text
            return {"summary": "S", "chunks": 1}

        monkeypatch.setattr(sv.A, "summarize", fake_summarize)
        A.save_config({"base_url": "http://x", "model": "m"})
        r = _client().post("/api/ai/summarize",
                           json={"scope": "selection", "page": 0, "rects": [[25, 320, 120, 345]]})
        assert r.status_code == 200
        assert "Line One here" in seen["text"]
        assert "Line Two here" not in seen["text"]
    finally:
        os.unlink(fd_path)


def test_ai_summarize_no_text_layer_returns_400_message():
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(300, 400))  # no text drawn
        c.showPage()
        c.save()
        fh.write(buf.getvalue())
    try:
        sv.open_pdf(sv.OpenReq(path=path))
        A.save_config({"base_url": "http://x", "model": "m"})
        r = _client().post("/api/ai/summarize", json={"scope": "all"})
        assert r.status_code == 400
        assert r.json()["detail"] == "テキスト層がありません（OCRを実行してください）"
    finally:
        os.unlink(path)


def test_ai_summarize_unconfigured_returns_400():
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(_pdf(["hello"]))
    try:
        sv.open_pdf(sv.OpenReq(path=path))
        r = _client().post("/api/ai/summarize", json={"scope": "all"})
        assert r.status_code == 400
        assert "AI設定" in r.json()["detail"]
    finally:
        os.unlink(path)


def test_ai_summarize_unknown_scope_400():
    r = _client().post("/api/ai/summarize", json={"scope": "bogus", "text": "x"})
    assert r.status_code == 400


def test_ai_summarize_endpoint_error_maps_502(monkeypatch):
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(_pdf(["hello"]))
    try:
        sv.open_pdf(sv.OpenReq(path=path))
        A.save_config({"base_url": "http://x", "model": "m"})

        def boom(*a, **k):
            raise RuntimeError("接続できません: refused")

        monkeypatch.setattr(sv.A, "summarize", boom)
        r = _client().post("/api/ai/summarize", json={"scope": "all"})
        assert r.status_code == 502
        assert "AIエンドポイントエラー" in r.json()["detail"]
    finally:
        os.unlink(path)


def test_ai_summarize_chunk_limit_maps_400(monkeypatch):
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(_pdf(["hello"]))
    try:
        sv.open_pdf(sv.OpenReq(path=path))
        A.save_config({"base_url": "http://x", "model": "m"})

        def too_long(*a, **k):
            raise ValueError("テキストが長すぎます")

        monkeypatch.setattr(sv.A, "summarize", too_long)
        r = _client().post("/api/ai/summarize", json={"scope": "all"})
        assert r.status_code == 400
        assert "長すぎ" in r.json()["detail"]
    finally:
        os.unlink(path)
