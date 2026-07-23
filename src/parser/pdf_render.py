"""PDF 페이지를 근거 좌표와 함께 PNG로 렌더링."""
from __future__ import annotations

import fitz


def render_highlighted_page(
    pdf_bytes: bytes,
    page_number: int,
    rects: list[tuple[float, float, float, float]],
    zoom: float = 1.5,
) -> bytes:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = document[page_number - 1]
        for rect in rects:
            page.draw_rect(
                fitz.Rect(*rect),
                color=(0.99, 0.69, 0.09),
                fill=(0.99, 0.69, 0.09),
                fill_opacity=0.28,
                width=1.2,
                overlay=True,
            )
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()
