"""Format detection and the `.mpp` position: native .mpp is NOT supported (it is a proprietary binary); the PM is told how to export."""
from __future__ import annotations

from typing import Optional

from .models import ParseError

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MPP_MESSAGE = ("Native .mpp files are not supported. In Microsoft Project use File > Save As > 'XML Format' (.xml) and upload that file, "
               "or export to Primavera .xer or CSV.")
MAX_BYTES = 25 * 1024 * 1024


def detect_format(filename: str, content: bytes) -> str:
    """-> 'XER' | 'MSPDI' | 'CSV'. Raises ParseError for .mpp and anything unrecognisable."""
    name = (filename or "").lower()
    if len(content) > MAX_BYTES:
        raise ParseError(f"File is larger than the {MAX_BYTES // (1024 * 1024)} MB limit.", "TOO_LARGE")
    if not content:
        raise ParseError("The file is empty.", "EMPTY_FILE")
    if name.endswith((".mpp", ".mpt", ".mpx")) or content.startswith(OLE2_MAGIC):
        raise ParseError(MPP_MESSAGE, "MPP_NOT_SUPPORTED")
    if name.endswith(".xer") or content.lstrip()[:6] == b"ERMHDR":
        return "XER"
    head = content[:4000]
    if name.endswith(".xml") or head.lstrip().startswith(b"<?xml") or head.lstrip().startswith(b"<"):
        return "MSPDI"
    if name.endswith((".csv", ".txt", ".tsv")):
        return "CSV"
    if content.startswith(b"PK"):
        raise ParseError("Excel workbooks are not accepted for schedule import; save the sheet as CSV.", "XLSX_NOT_SUPPORTED")
    raise ParseError("The file type is not recognised. Supported: Primavera .xer, Microsoft Project XML (.xml), CSV.", "UNKNOWN_FORMAT")
