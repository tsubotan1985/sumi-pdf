"""PDF encryption / decryption / permissions (AES-256).

Stirling-PDF parity for the crypto family:
- set password (user + owner), AES-128/256
- remove password
- permission flags (print / copy / modify / annotate)
"""
from __future__ import annotations

import os

import pymupdf as fitz

PERM_FLAGS = {
    "print": fitz.PDF_PERM_PRINT,
    "print_high_res": fitz.PDF_PERM_PRINT | getattr(fitz, "PDF_PERM_PRINT_HQ", fitz.PDF_PERM_PRINT),
    "copy": fitz.PDF_PERM_COPY,
    "modify": fitz.PDF_PERM_MODIFY,
    "annotate": fitz.PDF_PERM_ANNOTATE,
    "forms": fitz.PDF_PERM_FORM,
    "accessibility": fitz.PDF_PERM_ACCESSIBILITY,
    "assemble": fitz.PDF_PERM_ASSEMBLE,
}

ENCRYPTION = {"aes-256": fitz.PDF_ENCRYPT_AES_256, "aes-128": fitz.PDF_ENCRYPT_AES_128}


def is_encrypted(path: str) -> bool:
    doc = fitz.open(path)
    try:
        return bool(doc.needs_pass)
    finally:
        doc.close()


def pdf_permissions(path: str, password: str = "") -> dict:
    """Effective permissions of an (optionally opened) PDF."""
    doc = fitz.open(path)
    try:
        if doc.needs_pass and not doc.authenticate(password or ""):
            raise RuntimeError("wrong password")
        p = doc.permissions or 0
        return {"encrypted": bool(doc.is_encrypted),
                "flags": {k: bool(p & v) for k, v in PERM_FLAGS.items() if k != "print_high_res"},
                "raw": p}
    finally:
        doc.close()


def encrypt_pdf(path: str, out_path: str = "", user_pw: str = "", owner_pw: str = "",
                algorithm: str = "aes-256",
                allow: dict | None = None) -> dict:
    """Set encryption. allow: {print, copy, modify, annotate, forms} booleans
    (default: print+annotate+forms allowed, copy/modify denied)."""
    if not user_pw and not owner_pw:
        raise ValueError("user_pw or owner_pw required")
    allow = {"print": True, "annotate": True, "forms": True, "copy": False, "modify": False,
             **(allow or {})}
    perms = 0
    for k, v in allow.items():
        if v and k in PERM_FLAGS:
            perms |= PERM_FLAGS[k]
    doc = fitz.open(path)
    try:
        doc.save(out_path or path, encryption=ENCRYPTION.get(algorithm, fitz.PDF_ENCRYPT_AES_256),
                 user_pw=user_pw or None, owner_pw=owner_pw or user_pw or None,
                 permissions=perms, garbage=4, deflate=True)
    finally:
        doc.close()
    out = out_path or path
    return {"saved": out, "bytes": os.path.getsize(out), "encrypted": True,
            "algorithm": algorithm, "allow": allow}


def decrypt_pdf(path: str, out_path: str = "", password: str = "") -> dict:
    """Remove encryption (requires open password or owner password)."""
    doc = fitz.open(path)
    try:
        if doc.needs_pass and not doc.authenticate(password or ""):
            raise RuntimeError("wrong password")
        doc.save(out_path or path, encryption=fitz.PDF_ENCRYPT_NONE,
                 garbage=4, deflate=True)
    finally:
        doc.close()
    out = out_path or path
    return {"saved": out, "bytes": os.path.getsize(out), "encrypted": False}
