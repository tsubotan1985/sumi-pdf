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
- tesseract     Apache-2.0 — https://github.com/tesseract-ocr/tesseract
                (external install; the released EXE calls the system tesseract)
- tessdata_fast Apache-2.0 — https://github.com/tesseract-ocr/tessdata_fast
                bundled; license copy: tools/tessdata/LICENSE
- NDLOCR-Lite   National Diet Library, Japan — CC-BY-4.0
                https://github.com/ndl-lab/ndlocr-lite
                **Not bundled**: optional component fetched by
                `scripts/setup_third_party.sh` into a local, gitignored
                `third_party/` directory. It is neither part of this
                repository nor included in the released EXE build.

Fonts (fonts/, see fonts/LICENSES.md for the full list)
-------------------------------------------------------
- IPAex Gothic / IPAex Mincho   IPA Font License v1.0
                license copy: fonts/IPA_Font_License_Agreement_v1.0.txt
                (extracted verbatim from the official IPAexfont00401.zip)
- Noto Sans CJK JP              SIL Open Font License 1.1
- Liberation Sans / Serif (v2.1.5, OFL) SIL Open Font License 1.1
                license copy + per-font copyright lines: fonts/OFL.txt

OCR model files (NDLOCR-Lite, optional — not bundled)
-----------------------------------------------------
- DEIMv2, PARSeq, PaddleOCR components — Apache-2.0 (upstream: ndl-lab/ndlocr-lite)
- DINOv3 — Meta custom license (upstream: ndl-lab/ndlocr-lite; see
  third_party/ndlocr-lite/app/licenses/DINOv3-License.txt when installed)

Full license texts are kept in the upstream repositories listed above (and in
the local third_party/ndlocr-lite checkout when installed).
