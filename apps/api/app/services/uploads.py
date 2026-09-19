"""Upload validation. Every file is untrusted.

Checks, in order: size, extension allow-list, *content* sniffing (magic bytes —
the declared MIME type is not trusted), extension/content agreement, and for
PDFs a readable page count within the configured limit.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath

from app.config import settings
from app.core.errors import ApiError

EXTENSIONS = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str  # display only
    extension: str
    mime_type: str  # derived from content, not from the client
    size_bytes: int
    page_count: int


def safe_display_name(raw: str | None) -> str:
    """Keep only the final path component; drop control characters. Metadata only."""
    name = PurePath((raw or "").replace("\\", "/")).name
    name = "".join(ch for ch in unicodedata.normalize("NFC", name) if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip().lstrip(".")
    return (name or "document")[:200]


def sniff(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def pdf_page_count(data: bytes) -> int:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ApiError(415, "UNSUPPORTED_DOCUMENT", "Password-protected PDFs are not supported.")
        return len(reader.pages)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 — any parser failure means unreadable
        raise ApiError(415, "UNSUPPORTED_DOCUMENT", "The PDF could not be read. It may be damaged.") from exc


def validate_upload(raw_filename: str | None, data: bytes) -> ValidatedUpload:
    if not data:
        raise ApiError(400, "VALIDATION_ERROR", "The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise ApiError(413, "UPLOAD_TOO_LARGE", f"Files can be at most {settings.max_upload_mb} MB.")

    filename = safe_display_name(raw_filename)
    extension = PurePath(filename).suffix.lower()
    expected = EXTENSIONS.get(extension)
    if expected is None:
        raise ApiError(415, "UNSUPPORTED_DOCUMENT", "Supported file types: PDF, PNG, JPG/JPEG and WEBP.")

    actual = sniff(data)
    if actual is None or actual != expected:
        raise ApiError(
            415,
            "UNSUPPORTED_DOCUMENT",
            "The file contents do not match its extension. Upload the original PDF or image.",
        )

    pages = pdf_page_count(data) if actual == "application/pdf" else 1
    if pages < 1:
        raise ApiError(415, "UNSUPPORTED_DOCUMENT", "The PDF has no pages.")
    if pages > settings.max_pdf_pages:
        raise ApiError(
            413, "UPLOAD_TOO_LARGE", f"PDFs can have at most {settings.max_pdf_pages} pages for extraction."
        )
    return ValidatedUpload(filename=filename, extension=extension, mime_type=actual, size_bytes=len(data), page_count=pages)
