"""PDF 서류 로더 — PyMuPDF 기반 (Track B).

pypdf 대신 PyMuPDF(fitz)를 쓰는 이유: 텍스트뿐 아니라 **단어 단위 좌표**를 얻을 수 있어,
"이 서류 3페이지의 이 문장이 문제다"를 원본 PDF 위에 하이라이트할 수 있다.
이는 우리 핵심 차별화(근거 조문 하이라이트 + 서류 문제 지점 표시)의 전제다.

산출물:
- PdfDocument: 전체 텍스트 + 페이지별 텍스트/단어박스 (모듈 로컬 — 하이라이트용 상세 정보)
- to_parsed_document(): src/common/schemas.py의 계약(ParsedDocument)으로 변환 (verify 입력)

문서 유형 분류와 필드(항목) 추출은 이후 LLM 단계에서 채운다. 이 로더는 '읽기'만 책임진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from src.common.schemas import ParsedDocument


@dataclass
class WordBox:
    """페이지 내 단어 1개의 텍스트와 좌표(픽셀 유사 PDF 좌표계)."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Page:
    number: int  # 1부터 시작
    text: str
    words: list[WordBox] = field(default_factory=list)


@dataclass
class PdfDocument:
    """하이라이트까지 지원하는 상세 파싱 결과 (모듈 로컬)."""

    document_id: str
    pages: list[Page]

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    def locate(self, query: str) -> list[dict]:
        """문구가 등장하는 위치를 [{page, rects:[(x0,y0,x1,y1)...]}] 로 반환.

        하이라이트 렌더링용. 공백 차이를 무시하고 페이지별로 검색한다.
        """
        hits: list[dict] = []
        needle = "".join(query.split())
        if not needle:
            return hits
        for page in self.pages:
            rects = _search_page(page, needle)
            if rects:
                hits.append({"page": page.number, "rects": rects})
        return hits


def _search_page(page: Page, needle: str) -> list[tuple[float, float, float, float]]:
    """페이지의 단어들을 이어 붙여 needle(공백 제거)이 걸치는 단어 박스들을 찾는다."""
    joined = ""
    spans: list[tuple[int, int, WordBox]] = []  # (시작, 끝, 박스)
    for w in page.words:
        token = "".join(w.text.split())
        if not token:
            continue
        start = len(joined)
        joined += token
        spans.append((start, len(joined), w))

    results: list[tuple[float, float, float, float]] = []
    pos = joined.find(needle)
    while pos != -1:
        end = pos + len(needle)
        covered = [b for (s, e, b) in spans if s < end and e > pos]
        for b in covered:
            results.append((b.x0, b.y0, b.x1, b.y1))
        pos = joined.find(needle, pos + 1)
    return results


def load_pdf(source: str | Path | bytes, document_id: Optional[str] = None) -> PdfDocument:
    """PDF 경로 또는 바이트 → PdfDocument (텍스트 + 단어 좌표)."""
    if isinstance(source, (str, Path)):
        doc = fitz.open(source)
        doc_id = document_id or Path(source).stem
    else:
        doc = fitz.open(stream=source, filetype="pdf")
        doc_id = document_id or "uploaded.pdf"

    pages: list[Page] = []
    try:
        for i, page in enumerate(doc, start=1):
            words = [
                WordBox(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3])
                for w in page.get_text("words")
            ]
            pages.append(Page(number=i, text=page.get_text("text"), words=words))
    finally:
        doc.close()
    return PdfDocument(document_id=doc_id, pages=pages)


def to_parsed_document(
    pdf: PdfDocument, doc_type: str = "unknown", fields=None
) -> ParsedDocument:
    """모듈 로컬 PdfDocument → 공용 계약 ParsedDocument (verify 입력).

    doc_type/fields는 이후 LLM 분류·추출 단계에서 채운다. 지금은 원문만 실어 보낸다.
    """
    return ParsedDocument(
        document_id=pdf.document_id,
        doc_type=doc_type,
        fields=fields or [],
        raw_text=pdf.text,
    )
