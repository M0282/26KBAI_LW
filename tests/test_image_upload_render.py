"""이미지(JPG·PNG)로 올린 서류도 원문 하이라이트가 그려져야 한다.

실측 결함(이 테스트가 막는 것):
화면이 하이라이트를 그릴 때 '업로드 원본 바이트'를 그대로 PyMuPDF에 넘겼다.
PDF로 올리면 동작했지만 JPG로 올리면 원본이 PDF가 아니라서
ValueError("is no PDF")가 나고 화면 전체가 예외로 멈췄다.

판독 단계는 이미지를 PDF로 변환해 PdfDocument.pdf_bytes에 보관한다.
렌더링은 그 변환본을 써야 한다.

API를 호출하지 않는 결정론적 경로만 검증한다(무료·항상 실행 가능).
"""
from pathlib import Path

import fitz
import pytest

from src.parser.pdf_loader import load_pdf
from src.parser.pdf_render import render_highlighted_page

SAMPLE = Path("data/samples/demo/04_설명확인서.pdf")


def _jpg_bytes() -> bytes:
    """샘플 PDF 한 장을 JPG로 만든다(고정 파일을 저장소에 두지 않는다)."""
    document = fitz.open(str(SAMPLE))
    try:
        return document[0].get_pixmap(dpi=150).tobytes("jpeg")
    finally:
        document.close()


@pytest.mark.skipif(not SAMPLE.exists(), reason="샘플 서류가 없는 환경")
def test_image_upload_keeps_renderable_pdf():
    """이미지로 올려도 렌더링에 쓸 PDF 변환본이 남는다."""
    raw = _jpg_bytes()
    assert raw[:5] != b"%PDF-", "이 테스트는 PDF가 아닌 입력을 전제로 한다"

    pdf = load_pdf(raw, document_id="스캔본.jpg")
    assert pdf.pdf_bytes, "이미지 업로드에도 변환본이 있어야 한다"
    assert pdf.pdf_bytes[:5] == b"%PDF-"


@pytest.mark.skipif(not SAMPLE.exists(), reason="샘플 서류가 없는 환경")
def test_raw_image_bytes_cannot_be_rendered():
    """업로드 원본을 그대로 넘기면 실패한다 — 이게 화면을 멈추게 한 원인이다."""
    with pytest.raises(ValueError):
        render_highlighted_page(_jpg_bytes(), page_number=1, rects=[(0, 0, 10, 10)])


@pytest.mark.skipif(not SAMPLE.exists(), reason="샘플 서류가 없는 환경")
def test_converted_pdf_renders_highlight():
    """변환본으로는 하이라이트 PNG가 정상적으로 나온다."""
    pdf = load_pdf(_jpg_bytes(), document_id="스캔본.jpg")
    image = render_highlighted_page(pdf.pdf_bytes, page_number=1, rects=[(10, 10, 120, 40)])
    assert image[:4] == b"\x89PNG"
    assert len(image) > 1000
