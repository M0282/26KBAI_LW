"""서류 전수 감사 — 새 문서를 넣었을 때 '조용히 틀리는' 지점을 자동으로 찾는다.

문서를 하나씩 눈으로 보며 고치는 방식은 끝이 없다. 이 스크립트는 폴더째 넣으면
추출 결과를 원문과 대조해 **의심 지점만** 뽑아준다. 사람이 볼 것은 그 목록뿐이다.

검출하는 이상 징후
- CLASSIFY : 문서 유형 분류 실패(unknown)
- SCHEMA   : 유형별 고정 필드 목록과 불일치(같은 양식인데 결과가 달라지는 원인)
- GRADE    : 추출한 위험등급이 원문에 없음(환각) / 상품설명서인데 등급 미확인
- NAME     : 상품명이 조사·접속어로 시작하는 등 비정상
- GROUND   : 상품명이 원문에서 안 찾아짐(하이라이트 불가 + 상품 동일성 오판 위험)
- READ     : 텍스트를 거의 못 읽음(OCR·비전 실패)

실행:
    py -3 -m scripts.audit_documents --dir data/eval
    py -3 -m scripts.audit_documents "C:/경로/서류1.pdf" "C:/경로/서류2.jpg"
    py -3 -m scripts.audit_documents --dir data/eval --no-llm   # 규칙 기반만(무료)

종료코드: 이상 징후가 있으면 1 (CI에서 회귀 감시용으로 쓸 수 있다)
"""
from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.parser.financial_extractor import (
    DOC_TYPE_FIELDS,
    extract_document,
    field_map,
    grade_supported_by_text,
)
from src.parser.pdf_loader import load_pdf, to_parsed_document

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
_GRADE_DIGIT = re.compile(r"[1-6]\s*등급")


def _paths(argv: list[str]) -> list[Path]:
    paths: list[Path] = []
    iterator = iter(argv)
    for arg in iterator:
        if arg == "--dir":
            target = next(iterator, "")
            paths += [Path(p) for p in glob.glob(f"{target}/*")]
        elif not arg.startswith("--"):
            paths += [Path(p) for p in glob.glob(arg)]
    return sorted({p for p in paths if p.suffix.lower() in SUPPORTED})


def audit_one(path: Path, use_llm: bool) -> tuple[str, list[tuple[str, str]]]:
    """문서 1건을 감사해 (요약줄, [(구분, 메시지)…])를 반환."""
    pdf = load_pdf(str(path))
    result = extract_document(
        to_parsed_document(pdf), use_llm=use_llm,
        locator=pdf.locate, page_renderer=pdf.render_page,
    )
    fields = field_map(result)
    filled = {name: value for name, value in fields.items() if value}
    mode = "AI비전" if pdf.vision_applied else ("OCR" if pdf.ocr_applied else "텍스트")
    summary = (f"{path.name[:44]:44} {result.doc_type:20} {mode:6} "
               f"{len(filled)}/{len(fields)} {list(filled)}")

    found: list[tuple[str, str]] = []
    if len(pdf.text.strip()) < 30:
        found.append(("READ", "텍스트를 거의 못 읽음 — OCR/비전 확인 필요"))
    if result.doc_type == "unknown":
        found.append(("CLASSIFY", "문서 유형 분류 실패"))
    expected = DOC_TYPE_FIELDS.get(result.doc_type)
    if expected and list(fields) != list(expected):
        found.append(("SCHEMA", f"필드 목록이 유형 스키마와 불일치: {list(fields)}"))

    grade = fields.get("product_risk_level")
    if grade and not pdf.vision_applied and not grade_supported_by_text(grade, pdf.text):
        found.append(("GRADE", f"등급 '{grade}'이 원문에 없음 — 환각 의심"))
    if result.doc_type == "product_description" and not grade:
        # 원문에 등급 숫자 자체가 없는 상품(예: '중위험'만 서술한 DLS)은 정상이다.
        if _GRADE_DIGIT.search(pdf.text):
            found.append(("GRADE", "원문에 등급 표기가 있는데 추출하지 못함"))

    name = fields.get("product_name")
    if name:
        if len(name) < 4 or name.lstrip().startswith(("및 ", "의 ", "에 ", "을 ", "를 ")):
            found.append(("NAME", f"상품명이 비정상: {name!r}"))
        elif not pdf.locate(name):
            found.append(("GROUND", f"상품명이 원문에서 안 찾아짐: {name[:50]!r}"))
    return summary, found


def main(argv: list[str]) -> int:
    load_dotenv()
    use_llm = "--no-llm" not in argv
    paths = _paths(argv)
    if not paths:
        print(__doc__)
        return 1

    print(f"=== 서류 전수 감사 — {len(paths)}건 "
          f"({'LLM+비전' if use_llm else '규칙 기반만'}) ===\n")
    print(f'{"문서":44} {"유형":20} {"판독":6} 필드')
    print("-" * 116)

    issues: list[tuple[str, str, str]] = []
    for path in paths:
        try:
            summary, found = audit_one(path, use_llm)
        except Exception as exc:  # 읽기 실패도 감사 대상이다
            print(f"{path.name[:44]:44} 실패: {type(exc).__name__}")
            issues.append((path.name, "ERROR", f"{type(exc).__name__}: {exc}"))
            continue
        print(summary)
        issues += [(path.name, kind, message) for kind, message in found]

    print("\n" + "=" * 116)
    if not issues:
        print("이상 징후 없음 ✅")
        return 0
    print(f"이상 징후 {len(issues)}건")
    for name, kind, message in issues:
        print(f"  [{kind:8}] {name[:46]:48} {message}")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
