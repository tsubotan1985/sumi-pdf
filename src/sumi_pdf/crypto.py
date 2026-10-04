"""PDF encryption / decryption / permissions via pypdf (BSD-3) + cryptography.

Stirling-PDF parity: set password (user + owner, AES-256/128), remove password,
permission flags (print / copy / modify / annotate / forms / assemble / print-high).
"""
from __future__ import annotations

import io
import os

from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions as P

_ALGOS = {"AES-256": "AES-256", "AES-128": "AES-128", "RC4-128": "RC4-128"}


def _flag(print_=True, copy=False, modify=False, annotate=True,
          forms=True, assemble=False, print_high=True) -> int:
    f = 0
    if print_:
        f |= int(P.PRINT)
    if print_high:
        f |= int(P.PRINT_TO_REPRESENTATION)
    if modify:
        f |= int(P.MODIFY)
    if annotate:
        f |= int(P.ADD_OR_MODIFY)
    if copy:
        f |= int(P.EXTRACT)
    f |= int(P.EXTRACT_TEXT_AND_GRAPHICS)  # accessibility: always allowed
    if forms:
        f |= int(P.FILL_FORM_FIELDS)
    if assemble:
        f |= int(P.ASSEMBLE_DOC)
    return f


def is_encrypted(src) -> bool:
    r = PdfReader(_src(src))
    return bool(r.is_encrypted)


def _src(src):
    import io
    return io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src


def inspect(src, password: str = "") -> dict:
    """Encrypted? and effective permissions of a PDF."""
    from pypdf import PasswordType
    r = PdfReader(_src(src))
    enc = bool(r.is_encrypted)
    if enc:
        res = r.decrypt(password or "")
        if res == PasswordType.NOT_DECRYPTED:
            raise ValueError("wrong password")
    f = r.user_access_permissions
    if f is None:
        return {"encrypted": enc, "flags": None, "allow_all": not enc}
    f = int(f)
    def has(bit, name):
        try:
            return bool(f & int(getattr(P, name)))
        except AttributeError:
            return False
    return {"encrypted": enc, "flags": {
        "print": has(f, "PRINT"),
        "modify": has(f, "MODIFY"),
        "copy": has(f, "EXTRACT"),
        "annotate": has(f, "ADD_OR_MODIFY"),
        "forms": has(f, "FILL_FORM_FIELDS"),
        "assemble": has(f, "ASSEMBLE_DOC"),
        "print_high": has(f, "PRINT_TO_REPRESENTATION"),
    }}


def encrypt_pdf(src, out: str, user_password: str, owner_password: str | None = None,
                algo: str = "AES-256", *, print_=True, copy=True, modify=True,
                annotate=True, forms=True, assemble=True, print_high=True) -> dict:
    """Encrypt src (path or bytes) -> out. owner_password defaults to user_password."""
    if isinstance(src, (bytes, bytearray)):
        w = PdfWriter()
        w.append(PdfReader(io.BytesIO(src)))
    else:
        w = PdfWriter(clone_from=src)
    flag = _flag(print_=print_, copy=copy, modify=modify, annotate=annotate,
                 forms=forms, assemble=assemble, print_high=print_high)
    w.encrypt(user_password=user_password,
              owner_password=owner_password or user_password,
              algorithm=_ALGOS.get(algo, "AES-256"),
              permissions_flag=flag)
    with open(out, "wb") as fh:
        w.write(fh)
    return {"out": out, "bytes": os.path.getsize(out), "algo": _ALGOS.get(algo, "AES-256"),
            "encrypted": True}


def decrypt_pdf(src, out: str, password: str) -> dict:
    """Remove password from src (path or bytes) -> out. Raises on wrong password."""
    r = PdfReader(_src(src))
    if r.is_encrypted:
        res = r.decrypt(password)
        if res == 0:
            raise ValueError("wrong password")
    w = PdfWriter()
    w.append(r)
    with open(out, "wb") as fh:
        w.write(fh)
    return {"out": out, "bytes": os.path.getsize(out), "encrypted": False}


def pdf_permissions(src, out: str, owner_password: str, *,
                    print_=True, copy=True, modify=True, annotate=True,
                    forms=True, assemble=True, print_high=True) -> dict:
    """Change permission flags of an (owner-unlocked) PDF; keeps/sets owner pw."""
    if isinstance(src, (bytes, bytearray)):
        w = PdfWriter()
        w.append(PdfReader(io.BytesIO(src)))
    else:
        w = PdfWriter(clone_from=src)
    flag = _flag(print_=print_, copy=copy, modify=modify, annotate=annotate,
                 forms=forms, assemble=assemble, print_high=print_high)
    w.encrypt(user_password="", owner_password=owner_password,
              algorithm="AES-256", permissions_flag=flag)
    with open(out, "wb") as fh:
        w.write(fh)
    return {"out": out, "bytes": os.path.getsize(out),
            "permissions_flag": flag}
