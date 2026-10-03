"""What may be uploaded as a report or evidence file. The type is decided from the CONTENT (magic bytes and structure), must agree with the
extension the client gave, and is then the only thing the stored name's extension is derived from. Size limits are per type.
This is validation, not malware scanning: macro workbooks and PDFs with launch/script actions are refused, but a hostile file that
looks ordinary is not detected here (documented limitation; store files outside any web root and serve them as attachments only)."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Optional

from .errors import ApiError

MB = 1024 * 1024
LIMITS = {"pdf": 25 * MB, "image": 15 * MB, "xlsx": 15 * MB, "text": 5 * MB}
MAX_ZIP_UNCOMPRESSED = 120 * MB
MAX_ZIP_ENTRIES = 5000
MAX_IMAGE_PIXELS = 50_000_000
EXT_FAMILY = {"pdf": "pdf", "png": "image", "jpg": "image", "jpeg": "image", "xlsx": "xlsx", "csv": "text", "txt": "text"}
REPORT_KINDS = ("DAILY_REPORT", "SITE_REPORT")
IMAGE_KINDS = ("PHOTO",)


@dataclass(frozen=True)
class Detected:
    ext: str            # stored extension (pdf png jpg txt csv xlsx)
    mime: str
    family: str         # pdf image xlsx text


def safe_display_name(name: Optional[str]) -> str:
    n = (name or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    n = re.sub(r"[\x00-\x1f\x7f]", "", n).strip().lstrip(".")
    return (n or "upload")[:120]


def _text_ok(content: bytes) -> bool:
    if b"\x00" in content[:65536]:
        return False
    for enc in ("utf-8-sig", "cp1252"):
        try:
            content.decode(enc)
            return True
        except UnicodeDecodeError:
            continue
    return False


def detect(filename: str, content: bytes, kind: str) -> Detected:
    ext_in = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    fam_in = EXT_FAMILY.get(ext_in)
    if fam_in is None:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "Allowed files: PDF, PNG, JPEG, CSV, TXT, XLSX")
    head = content[:16]
    if head.startswith(b"%PDF-"):
        fam, ext, mime = "pdf", "pdf", "application/pdf"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        fam, ext, mime = "image", "png", "image/png"
    elif head.startswith(b"\xff\xd8\xff"):
        fam, ext, mime = "image", "jpg", "image/jpeg"
    elif head.startswith(b"PK\x03\x04"):
        fam, ext, mime = "xlsx", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fam_in == "text" and _text_ok(content):
        fam, ext, mime = "text", ext_in, ("text/csv" if ext_in == "csv" else "text/plain")
    else:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "The file content is not a supported PDF, image, spreadsheet or text file")
    if fam != fam_in:
        raise ApiError(422, "FILE_CONTENT_MISMATCH", "The file content does not match its extension")
    if len(content) > LIMITS[fam]:
        raise ApiError(413, "TOO_LARGE", f"{fam} files are limited to {LIMITS[fam] // MB} MB")
    if kind in IMAGE_KINDS and fam != "image":
        raise ApiError(422, "KIND_TYPE_MISMATCH", "A PHOTO must be a PNG or JPEG image")
    if kind in REPORT_KINDS and fam == "image":
        raise ApiError(422, "KIND_TYPE_MISMATCH", "Report files are PDF, CSV, TXT or XLSX; send photographs as PHOTO or EVIDENCE")
    if fam == "xlsx":
        _check_xlsx(content)
    elif fam == "pdf":
        _check_pdf(content)
    elif fam == "image":
        _check_image(content)
    return Detected(ext=ext, mime=mime, family=fam)


def _check_xlsx(content: bytes) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise ApiError(422, "CORRUPT_FILE", "The workbook is not a valid XLSX file") from e
    names = {i.filename for i in infos}
    if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "That archive is not an Excel workbook")
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
        raise ApiError(422, "FILE_TOO_COMPLEX", "The workbook is too large when unpacked")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise ApiError(415, "ACTIVE_CONTENT_REFUSED", "Workbooks with macros are not accepted")


def _check_pdf(content: bytes) -> None:
    if re.search(rb"/(?:Launch|JavaScript|JS)\b", content):
        raise ApiError(415, "ACTIVE_CONTENT_REFUSED", "PDFs with scripts or launch actions are not accepted")


def _check_image(content: bytes) -> None:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as im:
            w, h = im.size
            if w * h > MAX_IMAGE_PIXELS:
                raise ApiError(422, "FILE_TOO_COMPLEX", "The image has too many pixels")
            im.verify()
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(422, "CORRUPT_FILE", "The image could not be read") from e
