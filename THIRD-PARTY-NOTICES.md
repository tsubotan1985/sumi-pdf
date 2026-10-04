THIRD-PARTY NOTICES for SUMIPDF
===============================

Runtime dependencies (pip)
--------------------------
- pypdf              BSD-3-Clause            https://github.com/py-pdf/pypdf
- pypdfium2          Apache-2.0 OR BSD-3-Clause (PDFium, Google)  https://github.com/pypdfium2/pypdfium2
- reportlab          BSD-3-Clause            https://www.reportlab.com/opensource/
- Pillow             MIT-CMU (HPND)          https://python-pillow.org/
- fastapi / pydantic MIT                     https://fastapi.tiangolo.com/
- uvicorn            BSD-3-Clause            https://www.uvicorn.org/
- pywebview (app)    BSD-3-Clause            https://pywebview.flowrl.com/
- cryptography       Apache-2.0 OR BSD-3-Clause  https://github.com/pyca/cryptography
- PyInstaller (build) GPL-2.0-or-later with bootloader exception (output EXEs are not GPL)

OCR engines
-----------
- NDLOCR-Lite   National Diet Library, Japan — CC-BY-4.0
                https://github.com/ndl-lab/ndlocr-lite (bundled at third_party/ndlocr-lite)
- tesseract     Apache-2.0 — https://github.com/tesseract-ocr/tesseract
- tessdata_fast Apache-2.0 — https://github.com/tesseract-ocr/tessdata_fast
                license copy: tools/tessdata/LICENSE

Fonts (fonts/, see fonts/LICENSES.md for the full list)
-------------------------------------------------------
- IPAex Gothic / IPAex Mincho   IPA Font License v1.0
                license copy: fonts/IPA_Font_License_Agreement_v1.0.txt
                (extracted verbatim from the official IPAexfont00401.zip)
- Noto Sans CJK JP              SIL Open Font License 1.1
- Liberation Sans / Serif (v2.1.5, OFL) SIL Open Font License 1.1
                license copy + per-font copyright lines: fonts/OFL.txt

Model files (bundled with NDLOCR-Lite)
--------------------------------------
- DEIMv2, PARSeq, PaddleOCR components — Apache-2.0 (see third_party/ndlocr-lite)
- DINOv3 — see third_party/ndlocr-lite/app/licenses/DINOv3-License.txt

Full license texts are kept in third_party/ndlocr-lite/app/licenses/ and in the
upstream repositories listed above.
