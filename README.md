# Sumi PDF（墨）

ローカル完結のPDF墨消し・文字置換・OCRツールキット。Windowsデスクトップアプリ（WebView2）＋MCPサーバ＋HTTP API。

## 機能（v0.1）
- **本物の墨消し**: 黒塗りではなくテキストデータ・画像画素をPDFから削除（背景色になじませ塗りも可）
- **テキスト置換**: 既存文字をPDFデータから削除し、同一ベースラインに代替フォントで再挿入（フォントサブセット化）
- **フォントフォールバック**: MS明朝/ゴシック・Yu・Meiryo等をシステム参照、無ければ同梱 IPAex/Noto/Liberation で代替。文字化け名（lr¾© 等）も自動正規化
- **OCR**: tesseract / NDLOCR-Lite 自動検出（任意導入）
- **MCPサーバ**: `sumi-mcp` — Hermes / Claude Code から redact_pdf / replace_text / extract_text / ocr_pdf_page を直接呼べる
- **HTTP API**: `sumi-server`（127.0.0.1:8765）

## 実行（Windows）
```
py -3.12 -m venv .venv
.venv\Scripts\pip install -e .[app,build,test]
.venv\Scripts\python run_app.py      # WebView2ウィンドウ
.venv\Scripts\pyinstaller --noconfirm --windowed --name SumiPDF ^
  --add-data "fonts;fonts" --add-data "web;web" ^
  --collect-all uvicorn --collect-all webview --collect-all pymupdf run_app.py
```

## 開発（WSL側でも可）
```
PYTHONPATH=src python3 -m pytest tests -q
python3 tests/mcp_smoke.py
```

## MCP登録
```
hermes mcp add sumi-pdf -- python -m sumi_pdf.mcp_server
```

## 同梱フォントとライセンス
fonts/LICENSES.md 参照（IPA / SIL OFL / Liberation）。MS系フォントは同梱せずシステム参照のみ。
