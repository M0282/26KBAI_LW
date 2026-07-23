"""실물 상품설명서 추출 정확도 검증 하니스.

여러 PDF를 한 번에 돌려 핵심 필드 추출 결과를 요약표로 내고 실패를 플래그한다.
튜닝 → 재실행 루프용. 결과 캐시 덕에 같은 문서 재실행은 API 0원.

실행:
    py -3 -m scripts.eval_extraction data/eval/*.pdf          # LLM(기본)
    py -3 -m scripts.eval_extraction --rule data/eval/*.pdf   # 규칙기반만
    py -3 -m scripts.eval_extraction --dir data/eval          # 폴더 전체

상품설명서(product_description) 기준 핵심 필드: product_name, product_risk_level.
- product_risk_level 이 'N등급'으로 정규화되면 OK, 아니면 실패로 플래그.
- product_name 이 비었거나 비정상(너무 길거나 조사로 시작)으로 보이면 플래그.
"""
from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.parser.financial_extractor import extract_document, field_map
from src.parser.pdf_loader import load_pdf, to_parsed_document

_GRADE_RE = re.compile(r"[1-6]\s*등급")
# 값이 조사/접속어로 시작하면 규칙 정규식이 엉뚱한 문장을 잡은 신호
_JUNK_PREFIX = ("및 ", "에 ", "의 ", "을 ", "를 ", "이 ", "가 ", "뿐만", "들이 ", "으로 ")


def _risk_ok(value: str | None) -> bool:
    return bool(value) and bool(_GRADE_RE.search(value))


def _name_ok(value: str | None) -> bool:
    if not value:
        return False
    if value.startswith(_JUNK_PREFIX):
        return False
    return 2 <= len(value) <= 80


def _collect_paths(argv: list[str]) -> list[Path]:
    paths: list[Path] = []
    it = iter(argv)
    for a in it:
        if a == "--dir":
            d = next(it, "")
            paths += [Path(p) for p in glob.glob(f"{d}/*.pdf")]
        elif not a.startswith("--"):
            paths += [Path(p) for p in glob.glob(a)]
    return sorted({p for p in paths if p.suffix.lower() == ".pdf"})


def main(argv: list[str]) -> int:
    load_dotenv()
    use_llm = "--rule" not in argv
    paths = _collect_paths(argv)
    if not paths:
        print("사용법: py -3 -m scripts.eval_extraction [--rule] [--dir DIR] <pdf...>")
        return 1

    mode = "LLM" if use_llm else "규칙기반"
    print(f"=== 추출 검증 하니스 ({mode}) — {len(paths)}개 PDF ===\n")
    header = f"{'파일':28} {'유형':16} {'위험등급':10} {'상품명':30} {'플래그'}"
    print(header)
    print("-" * len(header))

    fail = 0
    for path in paths:
        try:
            pdf = load_pdf(str(path))
            if pdf.needs_ocr:
                # 스캔본(텍스트 레이어 없음) — 조용히 통과시키지 않고 명확히 표시
                print(f"{path.name[:28]:28} {'스캔본':16} {'-':10} {'-':30} ⚠️ OCR필요(수동검토)")
                fail += 1
                continue
            res = extract_document(to_parsed_document(pdf), use_llm=use_llm, locator=pdf.locate)
        except Exception as exc:
            print(f"{path.name[:28]:28} [오류: {type(exc).__name__}]")
            fail += 1
            continue
        fm = field_map(res)
        risk = fm.get("product_risk_level")
        name = fm.get("product_name")
        flags = []
        if res.doc_type == "product_description":
            if not _risk_ok(risk):
                flags.append("위험등급실패")
            if not _name_ok(name):
                flags.append("상품명이상")
        if res.doc_type == "unknown":
            flags.append("분류실패")
        if flags:
            fail += 1
        print(
            f"{path.name[:28]:28} {res.doc_type[:16]:16} "
            f"{str(risk)[:10]:10} {str(name)[:30]:30} {'⚠️ ' + ','.join(flags) if flags else 'OK'}"
        )

    print(f"\n총 {len(paths)}개 중 플래그 {fail}개 "
          f"({round((len(paths)-fail)/len(paths)*100)}% 정상)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
