"""Track B document loader for PDF/image, DOCX, and TXT inputs.

The existing :mod:`src.parser.pdf_loader` remains the single implementation for
PDF/image parsing, OCR, coordinates, and page rendering.  This module only adds
the DOCX/TXT adapters that existed in ``kb_rulelens_mvp`` and exposes one common
entry point for the Streamlit app.

Output continues to follow ``src.common.schemas.ParsedDocument``.  No shared
schema changes are required.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Iterable, Optional

from docx import Document as open_docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from src.common.schemas import ParsedDocument
from src.parser.pdf_loader import PdfDocument, load_pdf


PDF_AND_IMAGE_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"
}
TEXT_EXTENSIONS = {".docx", ".txt"}
_DUPLICATE_SUFFIX_RE = re.compile(r"\s+\(\d+\)$")


class UnsupportedDocumentError(ValueError):
    """Raised when an uploaded file type is outside the supported formats."""


class InvalidDocumentError(ValueError):
    """Raised when a DOCX/TXT file is empty, damaged, or cannot be decoded."""


@dataclass
class TextDocument:
    """Detailed non-PDF parsing result compatible with the Track B pipeline.

    DOCX/TXT files do not have stable PDF coordinates. ``locate`` therefore
    returns the source block number and an exact excerpt, while keeping the same
    ``[{page, rects, ...}]`` shape expected by the extractor and app.
    """

    document_id: str
    blocks: list[str]
    source_format: str
    scanned: bool = False
    ocr_applied: bool = False
    vision_applied: bool = False
    pdf_bytes: bytes | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(block for block in self.blocks if block.strip())

    @property
    def needs_ocr(self) -> bool:
        return False

    def render_page(self, page_number: int, dpi: int = 150) -> None:
        """Text formats have no raster page representation."""
        return None

    def locate(self, query: str) -> list[dict]:
        """Locate text while ignoring whitespace differences.

        ``page`` is fixed to 1 because these text formats have no reliable PDF
        pagination. ``block`` stores the ordered paragraph/table-row or line
        number used by the UI. ``rects`` stays empty because fixed coordinates
        do not exist.
        """
        needle = _compact(query)
        if not needle:
            return []

        hits: list[dict] = []
        for index, block in enumerate(self.blocks, start=1):
            if needle not in _compact(block):
                continue
            hits.append({
                "page": 1,
                "block": index,
                "rects": [],
                "excerpt": _excerpt(block, query),
                "location_type": "block" if self.source_format == "docx" else "line",
            })
        return hits


LoadedDocument = PdfDocument | TextDocument


def _compact(value: str) -> str:
    return "".join(value.split())


def _excerpt(block: str, query: str, radius: int = 80) -> str:
    """Return a readable source excerpt without fabricating missing text."""
    clean = " ".join(block.split())
    if not clean:
        return query

    direct = clean.find(query)
    if direct >= 0:
        start = max(0, direct - radius)
        end = min(len(clean), direct + len(query) + radius)
        return clean[start:end]

    # Whitespace-normalized matches cannot be mapped exactly without losing the
    # original layout. Returning the complete short block is safer and grounded.
    return clean[: radius * 2 + len(query)]


def _logical_suffix(file_name: str) -> str:
    """Read the extension even after Streamlit duplicate renaming.

    The app renames duplicate uploads as ``contract.docx (2)``. ``Path.suffix``
    would see ``.docx (2)`` and reject a valid document, so strip that display
    suffix before checking the real extension.
    """
    normalized = _DUPLICATE_SUFFIX_RE.sub("", file_name.strip())
    return Path(normalized).suffix.lower()


def _document_id(source: str | Path | bytes, file_name: str | None, document_id: str | None) -> str:
    if document_id:
        return document_id
    if file_name:
        return file_name
    if isinstance(source, (str, Path)):
        return Path(source).name
    return "uploaded"


def _source_suffix(source: str | Path | bytes, file_name: str | None) -> str:
    if file_name:
        return _logical_suffix(file_name)
    if isinstance(source, (str, Path)):
        return Path(source).suffix.lower()
    raise UnsupportedDocumentError("바이트 업로드에는 file_name이 필요합니다.")


def _iter_docx_blocks(document: DocxDocument) -> Iterable[str]:
    """Yield paragraphs and table rows in their original document order."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, document).text.strip()
            if text:
                yield text
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            for row in table.rows:
                values = [" ".join(cell.text.split()) for cell in row.cells]
                row_text = "\t".join(value for value in values if value)
                if row_text.strip():
                    yield row_text


def _open_docx_source(source: str | Path | bytes) -> DocxDocument:
    try:
        if isinstance(source, bytes):
            return open_docx(BytesIO(source))
        return open_docx(str(source))
    except Exception as exc:
        raise InvalidDocumentError("DOCX가 손상되었거나 올바른 DOCX 형식이 아닙니다.") from exc


def load_docx(
    source: str | Path | bytes,
    *,
    file_name: str | None = None,
    document_id: str | None = None,
) -> TextDocument:
    """Load DOCX body paragraphs and tables using ``python-docx``."""
    document = _open_docx_source(source)
    blocks = list(_iter_docx_blocks(document))
    if not any(block.strip() for block in blocks):
        raise InvalidDocumentError("DOCX에서 읽을 수 있는 본문이나 표 텍스트가 없습니다.")
    return TextDocument(
        document_id=_document_id(source, file_name, document_id),
        blocks=blocks,
        source_format="docx",
    )


def _decode_txt(data: bytes) -> str:
    # BOM-aware encodings first, then common Korean Windows encodings.
    for encoding in ("utf-8-sig", "utf-16", "cp949", "euc-kr"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise InvalidDocumentError("TXT 인코딩을 판별할 수 없습니다(UTF-8/UTF-16/CP949 지원).")


def load_txt(
    source: str | Path | bytes,
    *,
    file_name: str | None = None,
    document_id: str | None = None,
) -> TextDocument:
    """Load UTF-8, UTF-16, CP949, or EUC-KR text files."""
    try:
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
    except OSError as exc:
        raise InvalidDocumentError("TXT 파일을 읽을 수 없습니다.") from exc

    text = _decode_txt(data).replace("\x00", "")
    blocks = [line.strip() for line in text.splitlines() if line.strip()]
    if not blocks:
        raise InvalidDocumentError("TXT에 읽을 수 있는 텍스트가 없습니다.")
    return TextDocument(
        document_id=_document_id(source, file_name, document_id),
        blocks=blocks,
        source_format="txt",
    )


def load_document(
    source: str | Path | bytes,
    *,
    file_name: str | None = None,
    document_id: Optional[str] = None,
    ocr: str = "auto",
) -> LoadedDocument:
    """Load a supported upload without replacing the existing PDF parser.

    PDF/images are delegated unchanged to ``load_pdf`` so PyMuPDF coordinates,
    OCR, vision fallback, and highlighting remain owned by ``pdf_loader.py``.
    """
    suffix = _source_suffix(source, file_name)
    doc_id = _document_id(source, file_name, document_id)

    if suffix in PDF_AND_IMAGE_EXTENSIONS:
        return load_pdf(source, document_id=doc_id, ocr=ocr)
    if suffix == ".docx":
        return load_docx(source, file_name=file_name, document_id=doc_id)
    if suffix == ".txt":
        return load_txt(source, file_name=file_name, document_id=doc_id)
    raise UnsupportedDocumentError(
        f"지원하지 않는 형식입니다: {suffix or '(확장자 없음)'}"
    )


def to_parsed_document(
    document: LoadedDocument,
    doc_type: str = "unknown",
    fields=None,
) -> ParsedDocument:
    """Convert any Track B loader result to the shared interface contract."""
    return ParsedDocument(
        document_id=document.document_id,
        doc_type=doc_type,
        fields=fields or [],
        raw_text=document.text,
    )
