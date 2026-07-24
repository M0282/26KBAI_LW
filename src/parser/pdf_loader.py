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
    scanned: bool = False        # 텍스트 레이어 없는 스캔본(이미지) 여부
    ocr_applied: bool = False     # OCR로 텍스트를 복원했는지
    vision_applied: bool = False  # Tesseract가 못 읽어 LLM 비전으로 전사했는지

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    @property
    def needs_ocr(self) -> bool:
        """스캔본인데 OCR로 텍스트를 얻지 못한 상태(=수동검토/OCR 필요)."""
        return self.scanned and len(self.text.strip()) < 20

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


import os as _os


def _find_tessdata() -> str | None:
    """kor.traineddata가 있는 tessdata 폴더를 자동 탐색.

    한국어 OCR은 kor 데이터가 필요하다. 기본 설치(Program Files/tessdata)엔 보통
    eng만 있으므로, TESSDATA_PREFIX 또는 사용자 폴더(~/tessdata)를 우선 확인한다.
    """
    for d in (
        _os.environ.get("TESSDATA_PREFIX"),
        _os.path.expanduser("~/tessdata"),
        "C:/Program Files/Tesseract-OCR/tessdata",
    ):
        if d and _os.path.exists(_os.path.join(d, "kor.traineddata")):
            return d
    return None


def _ocr_page(page) -> tuple[str, list["WordBox"]]:
    """Tesseract(+한국어)가 있으면 페이지를 OCR해 텍스트+단어좌표 반환. 없으면 ('', [])."""
    tessdata = _find_tessdata()
    try:
        kwargs = {"flags": 0, "language": "kor+eng", "dpi": 200, "full": True}
        if tessdata:
            kwargs["tessdata"] = tessdata
        tp = page.get_textpage_ocr(**kwargs)
    except Exception:
        return "", []  # tesseract/한국어 데이터 미설치 등 → OCR 불가(needs_ocr로 표시됨)
    text = page.get_text("text", textpage=tp)
    words = [
        WordBox(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3])
        for w in page.get_text("words", textpage=tp)
    ]
    return text, words


_VISION_PROMPT = (
    "이 문서 이미지의 텍스트를 있는 그대로 전사(transcribe)하세요. "
    "설명·요약·추측 없이 문서에 실제로 보이는 텍스트만 출력하세요. "
    "보이지 않는 내용은 절대 지어내지 마세요."
)


def _vision_ocr(page) -> str:
    """Tesseract가 못 읽는 이미지를 LLM 비전으로 전사한다 (OCR 폴백).

    다크모드 스크린샷·저해상도 사진·장식 폰트에서 Tesseract는 한글을 자주 틀린다
    (예: '투자성향'→'투자성양', '적극투자형'→'적극투자영'). 이때만 비전을 호출한다.

    비용 규칙: 결과 캐시(내용 해시)로 재호출 0원, 일반 PDF는 이 경로에 오지 않는다.
    이미지 1장 ≈ 1.6k 토큰(haiku 기준 수 원). VISION_OCR=0 으로 끌 수 있다.
    반환값은 텍스트뿐 — 좌표가 없으므로 하이라이트는 Tesseract 좌표를 그대로 쓴다.
    """
    if _os.environ.get("VISION_OCR", "1") == "0" or not _os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    try:
        import base64
        import hashlib

        import anthropic

        from src.common.llm_cache import cached_text, make_key
    except Exception:
        return ""
    try:
        data = page.get_pixmap(dpi=150).tobytes("jpeg")
    except Exception:
        return ""
    model = _os.environ.get("VISION_MODEL", "claude-haiku-4-5")
    key = make_key("vision-ocr", model, hashlib.sha256(data).hexdigest())

    def _produce() -> str:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(data).decode(),
                    }},
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    try:
        return cached_text(key, _produce)
    except Exception:
        return ""  # 키 오류·네트워크 실패 → Tesseract 결과 유지


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}


def _open_any(source: str | Path | bytes, document_id: Optional[str]):
    """PDF/이미지(경로·바이트)를 fitz PDF 문서로 연다.

    이미지(스캔·사진·스크린샷)는 PDF로 변환해서 연다 → 이후 스캔 페이지로 감지되어
    OCR이 자동 적용된다. 업로더가 PDF뿐 아니라 이미지도 받을 수 있게 한다.

    세 번째 반환값은 '원본이 이미지였는지'다. 사진·스크린샷은 Tesseract 정확도가
    특히 낮아(다크모드·장식 폰트) 비전 폴백 판단에 쓴다.
    """
    if isinstance(source, (str, Path)):
        p = Path(source)
        doc_id = document_id or p.stem
        if p.suffix.lower() in _IMAGE_EXTS:
            img = fitz.open(str(p))
            data = img.convert_to_pdf()
            img.close()
            return fitz.open(stream=data, filetype="pdf"), doc_id, True
        return fitz.open(str(p)), doc_id, False
    # bytes
    doc_id = document_id or "uploaded"
    if source[:5] == b"%PDF-":
        return fitz.open(stream=source, filetype="pdf"), doc_id, False
    # PDF가 아니면 이미지로 간주 → PDF 변환
    img = fitz.open(stream=source, filetype="jpg")  # fitz가 실제 이미지 포맷 자동 감지
    data = img.convert_to_pdf()
    img.close()
    return fitz.open(stream=data, filetype="pdf"), doc_id, True


def load_pdf(
    source: str | Path | bytes,
    document_id: Optional[str] = None,
    ocr: str = "auto",
) -> PdfDocument:
    """PDF/이미지(경로·바이트) → PdfDocument (텍스트 + 단어 좌표).

    이미지는 PDF로 변환 후 처리(스캔 페이지로 감지 → OCR 자동 적용).
    ocr='auto': 텍스트 레이어가 없는 스캔 페이지는 Tesseract가 있으면 OCR로 복원하고,
        Tesseract가 실패(빈 결과)하거나 원본이 사진·스크린샷이면 LLM 비전으로 전사한다.
    ocr='off': OCR·비전 모두 시도 안 함(스캔본은 scanned=True, 텍스트 비어있음).
    스캔본을 조용히 빈 결과로 통과시키지 않고 needs_ocr로 드러내는 것이 목적.
    """
    doc, doc_id, from_image = _open_any(source, document_id)

    pages: list[Page] = []
    image_only = 0
    ocr_applied = False
    vision_applied = False
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            words = [
                WordBox(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3])
                for w in page.get_text("words")
            ]
            # 텍스트가 거의 없는데 이미지가 있으면 스캔(이미지-only) 페이지.
            if len(text.strip()) < 20 and page.get_images():
                image_only += 1
                if ocr != "off":
                    otext, owords = _ocr_page(page)
                    if otext.strip():
                        text, words, ocr_applied = otext, owords, True
                    # Tesseract가 아예 못 읽었거나, 원본이 사진·스크린샷이라
                    # 오독 위험이 큰 경우에만 비전 전사로 텍스트를 대체한다.
                    # 좌표(words)는 Tesseract 것을 그대로 둔다(비전은 좌표를 주지 않음).
                    if not otext.strip() or from_image:
                        vtext = _vision_ocr(page)
                        if len(vtext.strip()) > len(text.strip()) // 2:
                            text, vision_applied = vtext, True
            pages.append(Page(number=i, text=text, words=words))
    finally:
        doc.close()

    # 문서 전체 기준으로 스캔본 판정: 이미지-only 페이지가 과반이거나 원문이 거의 없음.
    # (정상 문서에 차트 이미지 페이지가 한둘 섞인 경우를 스캔본으로 오판하지 않도록)
    total_text = sum(len(p.text.strip()) for p in pages)
    scanned = bool(pages) and (image_only >= max(1, len(pages) * 0.5) or total_text < 50)
    return PdfDocument(
        document_id=doc_id,
        pages=pages,
        scanned=scanned,
        ocr_applied=ocr_applied,
        vision_applied=vision_applied,
    )


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
