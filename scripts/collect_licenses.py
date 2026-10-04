#!/usr/bin/env python3
"""Collect third-party license files for SUMIPDF distribution.

Run with the project's Windows virtualenv so that the collected set matches
the environment the executable is built from::

    .venv\\Scripts\\python.exe scripts\\collect_licenses.py

Output (all under the repository's ``licenses/`` directory)::

    licenses/<distribution>/<relative path>   license files for every installed dist
    licenses/Python/LICENSE.txt               CPython PSF license (base interpreter)
    licenses/SOURCES.md                        provenance: source URL + timestamp

The script reads metadata via ``importlib.metadata`` (PEP 566/639): every
distribution's declared ``License-File`` entries are copied, and any file whose
name looks like a license/notice/copyright file is also copied.  The set is
deduplicated and written to a fresh ``licenses/`` tree on each run.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "licenses"

# A file counts as a license artifact when its basename (or the last two path
# components) match this pattern.  Anchored loosely so variant names such as
# ``LICENSE.md``, ``COPYING.txt``, ``LICENCE``, ``NOTICE``, ``LicenseRef-*.txt``
# and ``LICENSE-3RD-PARTY.txt`` are all caught.
LICENSE_RE = re.compile(
    r"(^|[-_. ])(licen[sc]e|copying|notice|copyright|authors?|third[-_]?part|dep5)",
    re.IGNORECASE,
)
# Files above this size are almost certainly data, not license text.
MAX_LICENSE_BYTES = 2_000_000


def _mget(md, key, default=None):
    """``email.message``-style lookup that the type stubs don't declare."""
    getter = getattr(md, "get")  # Any: avoids both stubs and the __getitem__ warning
    return getter(key, default)


def _is_license_name(path: str) -> bool:
    """True when *path* looks like a license/notice artifact."""
    parts = str(path).replace("\\", "/").split("/")
    if not parts:
        return False
    base = parts[-1]
    tail = "/".join(parts[-2:])
    return bool(LICENSE_RE.search(base) or LICENSE_RE.search(tail))


def _dist_info_dir(dist: metadata.Distribution) -> Path | None:
    """Best-effort location of the distribution's ``.dist-info`` directory."""
    # Importlib exposes this via the private ``_path`` attribute for wheel
    # installs; fall back to locating a sentinel file otherwise.
    p = getattr(dist, "_path", None)
    if p is not None:
        return Path(str(p))
    for f in dist.files or []:
        s = str(f)
        if ".dist-info" in s:
            try:
                return Path(str(dist.locate_file(f))).parents[
                    len(Path(s).parts) - 1 - str(Path(s).parts).index(".dist-info")
                ]
            except Exception:
                pass
    return None


def _resolve_declared(dist: metadata.Distribution, rel: str) -> Path | None:
    """Resolve a declared ``License-File`` path to an existing file."""
    candidates: list[Path] = []
    try:
        candidates.append(Path(str(dist.locate_file(rel))))
    except Exception:
        pass
    di = _dist_info_dir(dist)
    if di is not None:
        candidates += [
            di / rel,
            di / "licenses" / rel,
            di / Path(rel).name,
            Path(str(dist.locate_file(""))) / rel,
        ]
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _source_url(dist: metadata.Distribution) -> str:
    """Pick the most useful source/repository URL for a distribution."""
    md = dist.metadata
    preferred = ("source", "repository", "homepage", "home", "source code", "code", "github")
    urls: dict[str, str] = {}
    for entry in md.get_all("Project-URL") or []:
        if "," in entry:
            label, _, url = entry.partition(",")
            urls[label.strip().lower()] = url.strip()
    for key in preferred:
        if key in urls:
            return urls[key]
    home = _mget(md, "Home-page")
    if home:
        return home.strip()
    if urls:
        return next(iter(urls.values()))
    name = _mget(md, "Name") or ""
    return f"https://pypi.org/project/{name}/" if name else ""


def _license_id(dist: metadata.Distribution) -> str:
    md = dist.metadata
    lex = _mget(md, "License-Expression")
    if lex:
        return lex.strip()
    lic = (_mget(md, "License") or "").strip()
    if lic and lic.lower() not in {"unknown", ""} and "\n" not in lic:
        return lic.replace("\n", " ")[:120]
    classifiers = [
        c.split("::")[-1].strip()
        for c in md.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    return " / ".join(dict.fromkeys(classifiers)) or "see file(s)"


def _dest_relative(src: Path, base: Path | None, used: set[str]) -> Path:
    """Pick a collision-free relative destination path for *src*."""
    if base is not None:
        try:
            rel = src.relative_to(base)
        except ValueError:
            rel = Path(src.name)
    else:
        rel = Path(src.name)
    # Drop a leading '<dist>.dist-info' component to keep names readable.
    if rel.parts and rel.parts[0].endswith(".dist-info"):
        rel = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path(src.name)
    # Drop a redundant leading 'licenses/' segment (PEP 639 wheel layout).
    if len(rel.parts) > 1 and rel.parts[0].lower() in {"licenses", "license", "licences"}:
        rel = Path(*rel.parts[1:])
    key = rel.as_posix()
    if key in used:
        rel = Path(src.parent.name) / src.name
        key = rel.as_posix()
        n = 2
        while key in used:
            rel = Path(f"{src.parent.name}-{n}") / src.name
            key = rel.as_posix()
            n += 1
    used.add(key)
    return rel


def collect_distribution(
    dist: metadata.Distribution, rows: list[dict], copied: list[str]
) -> dict:
    name = _mget(dist.metadata, "Name") or getattr(dist, "name", None) or "unknown"
    version = _mget(dist.metadata, "Version") or ""
    safe = re.sub(r"[^\w.\-]+", "_", name) or "unknown"
    dest_dir = OUT_DIR / safe
    try:
        base = Path(str(dist.locate_file("")))
    except Exception:
        base = None

    candidates: dict[str, Path] = {}

    for rel in dist.metadata.get_all("License-File") or []:
        p = _resolve_declared(dist, rel)
        if p is not None:
            candidates[str(p.resolve())] = p

    for f in dist.files or []:
        s = str(f)
        if not _is_license_name(s):
            continue
        try:
            p = Path(str(dist.locate_file(f)))
        except Exception:
            continue
        try:
            if not p.is_file() or p.stat().st_size == 0 or p.stat().st_size > MAX_LICENSE_BYTES:
                continue
        except OSError:
            continue
        candidates[str(p.resolve())] = p

    used: set[str] = set()
    count = 0
    for p in sorted(candidates.values(), key=lambda x: str(x).lower()):
        rel = _dest_relative(p, base, used)
        dst = dest_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst)
        copied.append(str(dst.relative_to(REPO_ROOT)))
        count += 1

    return {
        "name": name,
        "version": version,
        "license": _license_id(dist),
        "url": _source_url(dist),
        "files": count,
        "dir": safe if count else "",
    }


def collect_python_license() -> dict:
    """Copy the PSF license of the running (base) CPython interpreter."""
    base = Path(sys.base_prefix)
    for cand in ("LICENSE.txt", "LICENSE", "LICENCE.txt", "LICENCE"):
        src = base / cand
        if src.is_file():
            dst = OUT_DIR / "Python" / "LICENSE.txt"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            return {
                "name": f"CPython {sys.version.split()[0]} (PSF)",
                "version": sys.version.split()[0],
                "license": "PSF-2.0",
                "url": "https://www.python.org/",
                "files": 1,
                "dir": "Python",
                "path": str(src),
            }
    return {}


def _write_sources(rows: list[dict], special: list[dict]) -> None:
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Third-party license sources",
        "",
        f"Generated: {now}",
        "Generator: `scripts/collect_licenses.py` (importlib.metadata)",
        f"Interpreter: {sys.version.split()[0]} | base_prefix: `{sys.base_prefix}`",
        "",
        "Each subdirectory below holds the license/notice files that the",
        "corresponding installed distribution ships, copied verbatim. The source",
        "column is the distribution's declared project/source URL.",
        "",
        "## Installed distributions",
        "",
        "| Distribution | Version | License | Source URL | Files |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: x["name"].lower()):
        if not r.get("files"):
            continue
        url = r["url"] or "-"
        lines.append(
            f"| {r['name']} | {r['version']} | {r['license']} | {url} | {r['files']} |"
        )
    lines += [
        "",
        "## Special components",
        "",
        "| Component | Version | License | Source URL | Notes |",
        "|---|---|---|---|---|",
    ]
    for s in special:
        lines.append(
            f"| {s['name']} | {s.get('version','')} | {s.get('license','')} | "
            f"{s.get('url','')} | {s.get('note','')} |"
        )
    lines += [
        "",
        "## Fonts / OCR / PDFium (not pip distributions)",
        "",
        "- Bundled fonts (IPAex, Noto Sans CJK, Liberation) — see `fonts/LICENSES.md`",
        "  and `fonts/OFL.txt` (SIL OFL 1.1, verbatim).",
        "- tesseract `tessdata_fast` — Apache-2.0, copy at `tools/tessdata/LICENSE`.",
        "- PDFium (via pypdfium2) third-party notices — copied under `pypdfium2/`",
        "  (upstream: https://github.com/pypdfium2-team/pypdfium2, `LICENSES/`).",
        "- NDLOCR-Lite is **not bundled**: it is an optional external component",
        "  fetched with `scripts/setup_third_party.sh` (see `THIRD-PARTY-NOTICES.md`).",
        "",
    ]
    (OUT_DIR / "SOURCES.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    copied: list[str] = []
    for dist in metadata.distributions():
        try:
            rows.append(collect_distribution(dist, rows, copied))
        except Exception as exc:  # never abort the whole collection for one dist
            name = _mget(dist.metadata, "Name", "?")
            print(f"WARN: {name}: {exc}", file=sys.stderr)

    special: list[dict] = []

    py = collect_python_license()
    if py:
        py["note"] = f"CPython license copied from {py.get('path','')}"
        special.append(py)

    # Explicitly surface the PDFium third-party bundle that pypdfium2 ships.
    pdfium = OUT_DIR / "pypdfium2" / "LicenseRef-PdfiumThirdParty.txt"
    special.append(
        {
            "name": "PDFium (via pypdfium2) third-party notices",
            "version": next((r["version"] for r in rows if r["name"].lower() == "pypdfium2"), ""),
            "license": "BSD-3-Clause (PDFium) + bundled third-party",
            "url": "https://github.com/pypdfium2-team/pypdfium2",
            "note": "copied to licenses/pypdfium2/ and licenses/pypdfium2/LICENSE*"
            if pdfium.is_file()
            else "WARNING: LicenseRef-PdfiumThirdParty.txt not found in pypdfium2",
        }
    )

    _write_sources(rows, special)

    total = len(copied)
    print(f"Collected {total} license file(s) for {sum(1 for r in rows if r.get('files'))} distributions.")
    print(f"Output: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
