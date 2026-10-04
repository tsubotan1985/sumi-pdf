"""License-compliance regression tests (Task 9).

Two groups:
  * ``fonts/OFL.txt`` must contain the *verbatim* canonical SIL OFL 1.1 text
    (structure: PREAMBLE / DEFINITIONS / PERMISSION & CONDITIONS with exactly
    five conditions / TERMINATION / DISCLAIMER) plus per-font copyright notices.
  * The required license artifacts must exist on disk, including the files
    collected by ``scripts/collect_licenses.py`` into ``licenses/``.

These tests read only repository files; they need no network or third-party
imports, so they run under both the Windows venv and WSL python.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OFL = REPO / "fonts" / "OFL.txt"

# sha256 of the verbatim license text (everything from the "This Font Software
# is licensed under ..." lead-in to the end), captured from the authoritative
# source https://openfontlicense.org/documents/OFL.txt.
CANONICAL_BODY_SHA256 = "fb755fa3280d1d215a20df2e64fe6a0f75f8208439b11671978214aae60d2997"
BODY_START = "This Font Software is licensed under the SIL Open Font License, Version 1.1."

REQUIRED_FILES = [
    "LICENSE",
    "THIRD-PARTY-NOTICES.md",
    "fonts/OFL.txt",
    "fonts/LICENSES.md",
    "fonts/IPA_Font_License_Agreement_v1.0.txt",
    "tools/tessdata/LICENSE",
    "licenses/SOURCES.md",
    "licenses/Python/LICENSE.txt",
    "licenses/pypdfium2/LicenseRef-PdfiumThirdParty.txt",
]


def _ofl_text() -> str:
    return OFL.read_text(encoding="utf-8")


def _ofl_body() -> str:
    text = _ofl_text()
    assert BODY_START in text, "OFL lead-in line missing"
    return text[text.index(BODY_START):]


# --- fonts/OFL.txt: verbatim text -----------------------------------------


def test_ofl_file_exists_and_nonempty():
    assert OFL.is_file()
    assert OFL.stat().st_size > 3000


def test_ofl_body_is_verbatim_canonical():
    body = _ofl_body()
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert digest == CANONICAL_BODY_SHA256, (
        "OFL body was modified; regenerate from the official text"
    )


def test_ofl_title_present():
    body = _ofl_body()
    assert "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007" in body
    assert "PREAMBLE" in body


def test_ofl_section_order():
    body = _ofl_body()
    sections = ["DEFINITIONS", "PERMISSION & CONDITIONS", "TERMINATION", "DISCLAIMER"]
    positions = [body.index(s) for s in sections]
    assert positions == sorted(positions), "OFL sections out of order"


def test_ofl_has_exactly_five_conditions():
    body = _ofl_body()
    for n in range(1, 6):
        assert re.search(rf"^{n}\)\s", body, re.MULTILINE), f"OFL condition {n}) missing"
    assert not re.search(r"^6\)\s", body, re.MULTILINE), "OFL has a bogus 6th condition"


def test_ofl_termination_and_disclaimer_text():
    body = _ofl_body()
    # Whitespace-normalized: the canonical text wraps these sentences.
    flat = " ".join(body.split())
    assert "This license becomes null and void if any of the above conditions are not met." in flat
    assert 'THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in flat


def test_ofl_reserved_font_name_condition_present():
    body = _ofl_body()
    assert "Reserved Font" in body
    assert "No Modified Version of the Font Software may use the Reserved Font" in body


def test_ofl_has_no_fabricated_wording():
    """The previous LLM-fabricated text contained these phrases."""
    body = _ofl_body()
    assert "making money off the Font Software" not in body
    assert "authorship is clearly acknowledged" not in body


def test_ofl_header_has_copyright_and_sources():
    text = _ofl_text()
    # fontTools-measured nameID 0 values for the bundled fonts.
    assert "\u00a9 2014-2021 Adobe" in text
    assert "2010 Google" in text and "2012 Red Hat" in text
    # provenance URLs
    assert "https://openfontlicense.org/documents/OFL.txt" in text
    assert "https://github.com/notofonts/noto-cjk" in text
    assert "https://github.com/liberationfonts/liberation-fonts" in text


# --- required artifacts ----------------------------------------------------


def test_required_license_files_exist():
    missing = [rel for rel in REQUIRED_FILES if not (REPO / rel).is_file()]
    assert not missing, f"missing license artifacts: {missing}"


def test_collected_licenses_nonempty():
    files = [p for p in (REPO / "licenses").rglob("*") if p.is_file()]
    # 200+ license files collected from the installed distributions.
    assert len(files) > 200, f"only {len(files)} collected license files"


def test_python_psf_license_content():
    text = (REPO / "licenses" / "Python" / "LICENSE.txt").read_text(encoding="utf-8")
    assert "PYTHON SOFTWARE FOUNDATION" in text or "PSF" in text


def test_pdfium_third_party_content():
    text = (REPO / "licenses" / "pypdfium2" / "LicenseRef-PdfiumThirdParty.txt").read_text(
        encoding="utf-8"
    )
    assert "libpng" in text.lower() and "Copyright" in text


def test_sources_md_has_url_and_timestamp():
    text = (REPO / "licenses" / "SOURCES.md").read_text(encoding="utf-8")
    assert re.search(r"Generated: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", text)
    assert "https://" in text
    assert "pypdfium2" in text


# --- notices match reality (NDLOCR is not bundled) -------------------------


def test_third_party_notices_do_not_claim_ndlocr_bundled():
    text = (REPO / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    assert "bundled at third_party" not in text
    assert "DINOv3" not in text or "not bundled" in text.lower()
    assert "not bundled" in text.lower()


def test_readme_does_not_claim_ndlocr_models_bundled():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "NDLOCR-Lite\u30e2\u30c7\u30eb\u306f\u540c\u68b1\u30d3\u30eb\u30c9\u306b\u542b\u307e\u308c\u307e\u3059" not in text
    assert "\u540c\u68b1\u30d3\u30eb\u30c9\u306b\u542b\u307e\u308c\u307e\u3059" not in text
