"""findredact: テキスト検索→候補一覧→一括墨消し (Task 5).

- find_candidates: 候補座標・context・空結果・複数ページ
- /api/find, /api/redact-bulk: サーバ契約
"""
import io

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from sumi_pdf import findredact as FR
from sumi_pdf import fonts as F
from sumi_pdf import pdfio as P
from sumi_pdf import redact as R
from sumi_pdf import server as sv

IPA = F.bundled_path("ipaexg.ttf")


def _pdf(pages: dict) -> bytes:
    """pages: {pno: [line, ...]} — IPAexGothic 14pt, 上から30pt間隔。"""
    pdfmetrics.registerFont(TTFont("IPA", IPA))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    for pno in sorted(pages):
        c.setFont("IPA", 14)
        y = 760
        for line in pages[pno]:
            c.drawString(60, y, line)
            y -= 30
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="module")
def doc() -> bytes:
    return _pdf({
        0: ["御見積書　株式会社サンプル　担当：墨野 消男",
            "連絡先 070-1316-8712",
            "株式会社サンプル　秘書室",
            "A" * 30 + "NEEDLE" + "B" * 30],
        1: ["株式会社サンプル　第二営業部",
            "担当：墨野 消男"],
    })


# ------------------------------------------------------- 候補座標

def test_candidate_rect_matches_pdfio_search(doc):
    hits = FR.find_candidates(doc, "070-1316-8712")
    assert len(hits) == 1
    h = hits[0]
    occ = P.search(doc, 0, "070-1316-8712")[0]
    assert h["page"] == 0
    assert (h["x0"], h["y0"], h["x1"], h["y1"]) == pytest.approx(tuple(occ.rect))
    assert h["x1"] > h["x0"] > 0 and h["y1"] > h["y0"]


def test_candidate_rect_is_usable_for_redaction(doc):
    """候補rectをそのまま墨消しに渡して文字が消える（座標系が一致）。"""
    h = FR.find_candidates(doc, "070-1316-8712")[0]
    out = R.redact(doc, 0, [(h["x0"], h["y0"], h["x1"], h["y1"])])
    txt = P.extract_text(out["bytes"], 0).replace("\n", "")
    assert "070-1316-8712" not in txt
    assert "株式会社サンプル" in txt  # 周囲は無傷


# ------------------------------------------------------- context

def test_context_is_20_chars_each_side():
    data = _pdf({0: ["A" * 30 + "NEEDLE" + "B" * 30]})
    (h,) = FR.find_candidates(data, "NEEDLE")
    assert h["context"] == "A" * 20 + "NEEDLE" + "B" * 20
    assert len(h["context"]) == 20 + len("NEEDLE") + 20


def test_context_keeps_match_and_flattens_newlines(doc):
    hits = FR.find_candidates(doc, "株式会社サンプル")
    p0 = [h for h in hits if h["page"] == 0][0]  # 1行目の株
    assert p0["context"].startswith("御見積書　")
    assert "株式会社サンプル" in p0["context"]
    assert "担当" in p0["context"]
    assert "\n" not in p0["context"] and "\r" not in p0["context"]


# ------------------------------------------------------- 空結果・ページ跨ぎ

def test_no_match_returns_empty(doc):
    assert FR.find_candidates(doc, "存在しない文字列XYZ") == []


def test_empty_query_returns_empty(doc):
    assert FR.find_candidates(doc, "") == []


def test_pages_spanned_in_order(doc):
    hits = FR.find_candidates(doc, "株式会社サンプル")
    assert [h["page"] for h in hits] == [0, 0, 1]


def test_multiple_hits_on_one_page_are_in_reading_order(doc):
    hits = FR.find_candidates(doc, "株式会社サンプル", page=0)
    assert len(hits) == 2
    assert hits[0]["y0"] > hits[1]["y0"]  # y上向き: 上の行が先


def test_page_restriction_and_range_check(doc):
    hits = FR.find_candidates(doc, "株式会社サンプル", page=1)
    assert [h["page"] for h in hits] == [1]
    with pytest.raises(ValueError):
        FR.find_candidates(doc, "株式会社サンプル", page=2)  # 2ページしか無い


# ------------------------------------------------------- サーバ契約

@pytest.fixture(autouse=True)
def _isolated_state():
    old = dict(sv.S)
    yield
    sv.S.clear()
    sv.S.update(old)


@pytest.fixture()
def client(doc, tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(doc)
    sv.open_pdf(sv.OpenReq(path=str(p)))
    return TestClient(sv.app)


def test_api_find_returns_candidates(client):
    j = client.post("/api/find", json={"q": "株式会社サンプル"})
    assert j.status_code == 200, j.text
    matches = j.json()["matches"]
    assert [m["page"] for m in matches] == [0, 0, 1]
    assert set(matches[0].keys()) == {"page", "x0", "y0", "x1", "y1", "context"}


def test_api_find_current_page_only(client):
    j = client.post("/api/find", json={"q": "株式会社サンプル", "page": 1})
    assert [m["page"] for m in j.json()["matches"]] == [1]


def test_api_find_page_out_of_range_404(client):
    assert client.post("/api/find", json={"q": "株式会社サンプル", "page": 9}).status_code == 404


def test_api_redact_bulk_across_pages(client):
    found = client.post("/api/find", json={"q": "株式会社サンプル"}).json()["matches"]
    rects = [{"page": m["page"], "x0": m["x0"], "y0": m["y0"],
              "x1": m["x1"], "y1": m["y1"]} for m in found]
    j = client.post("/api/redact-bulk", json={"rects": rects})
    assert j.status_code == 200, j.text
    body = j.json()
    assert set(body.keys()) == {"applied"}
    assert body["applied"]["dropped"] >= 3 and body["applied"]["filled"] == 3
    assert sv.S["dirty"] is True
    for pno in (0, 1):
        assert "株式会社サンプル" not in P.extract_text(sv.S["work"], pno)


def test_api_redact_bulk_empty_rects_is_noop(client):
    before = sv.S["work"]
    j = client.post("/api/redact-bulk", json={"rects": []})
    assert j.json() == {"applied": {"dropped": 0, "filled": 0}}
    assert sv.S["work"] == before and sv.S["dirty"] is False


def test_api_redact_bulk_bad_page_404(client):
    r = client.post("/api/redact-bulk", json={"rects": [{"page": 7, "x0": 0, "y0": 0, "x1": 10, "y1": 10}]})
    assert r.status_code == 404
