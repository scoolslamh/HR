from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import BinaryIO, Protocol

from django.conf import settings

from .exceptions import ImportFileValidationError
from .identity import redact_potential_national_ids


XLSX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_REQUIRED_PARTS = {
    "[content_types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
}
_PROHIBITED_PART_PREFIXES = (
    "xl/activex/",
    "xl/embeddings/",
    "xl/externallinks/",
)
_PROHIBITED_PART_NAMES = {
    "xl/vbaproject.bin",
    "xl/vbaprojectsignature.bin",
}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")


class UploadedFileLike(Protocol):
    name: str

    def chunks(self, chunk_size: int | None = None): ...


@dataclass(frozen=True, slots=True)
class ValidatedXlsx:
    content: bytes
    original_filename: str
    file_sha256: str
    file_size_bytes: int
    mime_type: str = XLSX_MIME_TYPE


def _max_upload_bytes() -> int:
    configured = getattr(settings, "EMPLOYEE_IMPORT_MAX_BYTES", 5 * 1024 * 1024)
    try:
        maximum = int(configured)
    except (TypeError, ValueError) as exc:
        raise ImportFileValidationError(
            "حد حجم ملف الاستيراد غير مضبوط بصورة صحيحة.",
            code="invalid_upload_limit",
        ) from exc
    if maximum <= 0:
        raise ImportFileValidationError(
            "حد حجم ملف الاستيراد غير مضبوط بصورة صحيحة.",
            code="invalid_upload_limit",
        )
    return maximum


def _safe_filename(name: object) -> str:
    filename = PurePath(str(name or "employees.xlsx").replace("\\", "/")).name
    filename = _CONTROL_CHARACTERS.sub("", filename).strip()
    filename = redact_potential_national_ids(filename)
    return (filename or "employees.xlsx")[:255]


def _read_upload(uploaded_file: UploadedFileLike | BinaryIO, maximum: int) -> bytes:
    content = bytearray()
    if hasattr(uploaded_file, "chunks"):
        chunks = uploaded_file.chunks(64 * 1024)
    elif hasattr(uploaded_file, "read"):
        chunks = iter(lambda: uploaded_file.read(64 * 1024), b"")
    else:
        raise ImportFileValidationError(code="invalid_upload")
    for chunk in chunks:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ImportFileValidationError(code="invalid_upload")
        content.extend(chunk)
        if len(content) > maximum:
            raise ImportFileValidationError(
                "حجم ملف الاستيراد يتجاوز الحد المسموح.",
                code="file_too_large",
            )
    if not content:
        raise ImportFileValidationError(
            "ملف الاستيراد فارغ.",
            code="empty_file",
        )
    return bytes(content)


def _validate_openxml_container(content: bytes, maximum: int) -> None:
    if not content.startswith(b"PK"):
        raise ImportFileValidationError(code="invalid_xlsx_signature")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if archive.testzip() is not None:
                raise ImportFileValidationError(code="corrupt_xlsx")
            infos = archive.infolist()
            names = {info.filename.replace("\\", "/").lower() for info in infos}
            if not _REQUIRED_PARTS.issubset(names):
                raise ImportFileValidationError(code="invalid_openxml_structure")
            if _PROHIBITED_PART_NAMES.intersection(names) or any(
                name.startswith(_PROHIBITED_PART_PREFIXES) for name in names
            ):
                raise ImportFileValidationError(
                    "لا يسمح بملفات تحتوي وحدات ماكرو أو محتوى مضمّن.",
                    code="unsafe_openxml_content",
                )

            total_uncompressed = 0
            max_uncompressed = max(maximum * 20, 20 * 1024 * 1024)
            for info in infos:
                normalized_name = info.filename.replace("\\", "/")
                if normalized_name.startswith("/") or ".." in normalized_name.split("/"):
                    raise ImportFileValidationError(code="unsafe_openxml_path")
                if info.flag_bits & 0x1:
                    raise ImportFileValidationError(code="encrypted_openxml_part")
                total_uncompressed += info.file_size
                if total_uncompressed > max_uncompressed:
                    raise ImportFileValidationError(
                        "محتوى ملف الاستيراد أكبر من الحد الآمن.",
                        code="expanded_file_too_large",
                    )
                if info.compress_size and info.file_size / info.compress_size > 250:
                    raise ImportFileValidationError(code="unsafe_compression_ratio")

            content_types = archive.read("[Content_Types].xml")
            lowered_content_types = content_types.lower()
            if (
                b"macroenabled" in lowered_content_types
                or b"vbaproject" in lowered_content_types
            ):
                raise ImportFileValidationError(
                    "لا يسمح بملفات تحتوي وحدات ماكرو.",
                    code="macro_enabled_workbook",
                )
            expected_type = (
                b"application/vnd.openxmlformats-officedocument."
                b"spreadsheetml.sheet.main+xml"
            )
            if expected_type not in lowered_content_types:
                raise ImportFileValidationError(code="invalid_workbook_content_type")

            for info in infos:
                if not info.filename.lower().endswith((".xml", ".rels")):
                    continue
                xml_content = archive.read(info)
                lowered_xml = xml_content.lower()
                if b"<!doctype" in lowered_xml or b"<!entity" in lowered_xml:
                    raise ImportFileValidationError(code="unsafe_xml_content")
    except ImportFileValidationError:
        raise
    except (KeyError, OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ImportFileValidationError(code="invalid_xlsx_container") from exc


def validate_xlsx_upload(uploaded_file: UploadedFileLike | BinaryIO) -> ValidatedXlsx:
    filename = _safe_filename(getattr(uploaded_file, "name", "employees.xlsx"))
    if not filename.lower().endswith(".xlsx"):
        raise ImportFileValidationError(
            "يسمح حاليًا بملفات xlsx فقط.",
            code="unsupported_extension",
        )
    maximum = _max_upload_bytes()
    content = _read_upload(uploaded_file, maximum)
    _validate_openxml_container(content, maximum)
    return ValidatedXlsx(
        content=content,
        original_filename=filename,
        file_sha256=hashlib.sha256(content).hexdigest(),
        file_size_bytes=len(content),
    )


def random_storage_key(identifier: uuid.UUID | None = None) -> str:
    prefix = str(
        getattr(settings, "EMPLOYEE_IMPORT_STORAGE_PREFIX", "employee-imports")
    ).strip("/\\")
    return f"{prefix}/{(identifier or uuid.uuid4()).hex}.bin"
