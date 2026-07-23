"""엔드투엔드 데모 러너 — PDF 서류 패키지 → 검증 리포트.

전체 파이프라인을 하나로 잇는 통합 진입점:
    PDF 로드(parser) → ParsedDocument → 검증(verify, 조문 검색+LLM) → 리포트 출력

실행 (레포 루트에서):
    py -3 -m scripts.demo_verify <pdf1> <pdf2> ...        # 오프라인(무료) 기본
    py -3 -m scripts.demo_verify --llm <pdf1> <pdf2> ...  # 실제 LLM 판정

주의: 필드 추출(_extract_fields)은 임시 규칙 기반이다. 문서 유형 분류 + 정밀 필드
추출은 Track B의 analyzer(친구 kb_rulelens의 ai_analyzer 이식 예정)가 대체한다.
"""
from __future__ import annotations

import re
import sys

from dotenv import load_dotenv

from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
from src.ingest.index import ArticleIndex
from src.parser.pdf_loader import load_pdf
from src.verify.engine import VerifyEngine
from src.verify.llm import OfflineClient, get_client

_ICON = {"risk": "[위험]", "missing": "[누락]", "warning": "[확인]", "pass": "[적합]"}


def _extract_fields(text: str) -> tuple[str, list[ParsedField]]:
    """임시 필드 추출 (Track B analyzer가 대체 예정).

    데모에 필요한 최소 신호(문서유형·투자성향·위험등급)만 규칙으로 뽑는다.
    """
    fields: list[ParsedField] = []
    tendency = re.search(r"투자성향[^\n]*?(안정형|안정추구형|위험중립형|적극투자형|공격투자형)", text)
    if tendency:
        fields.append(ParsedField(name="고객 투자성향", value=tendency.group(1), page=1))
    risk = re.search(r"위험등급[:\s]*([0-9]등급|매우\s*높은\s*위험|고위험|높은\s*위험)", text)
    if risk:
        fields.append(ParsedField(name="상품 위험등급", value=risk.group(1).strip(), page=1))

    if "적합성" in text or "투자성향" in text:
        doc_type = "적합성 진단표"
    elif "설명서" in text or "위험등급" in text:
        doc_type = "상품설명서"
    else:
        doc_type = "판매 서류"
    return doc_type, fields


def load_package(paths: list[str]) -> list[ParsedDocument]:
    docs: list[ParsedDocument] = []
    for path in paths:
        pdf = load_pdf(path)
        doc_type, fields = _extract_fields(pdf.text)
        docs.append(
            ParsedDocument(
                document_id=pdf.document_id,
                doc_type=doc_type,
                fields=fields,
                raw_text=pdf.text,
            )
        )
    return docs


def print_report(report) -> None:
    print("=" * 60)
    print(f"검증 대상: {report.doc_type}")
    print(f"종합: {report.summary}")
    print(f"차단성 이슈: {'있음' if report.has_blocker else '없음'} (총 {len(report.checks)}건)")
    print("=" * 60)
    for c in report.checks:
        print(f"\n{_ICON[c.status.value]} {c.rule_id}")
        print(f"  설명: {c.description}")
        if c.document_excerpt:
            print(f"  서류 근거: {c.document_excerpt[:80]}")
        if c.evidence_clause:
            print(f"  법령 근거: {c.evidence_clause}")
        if c.suggestion:
            print(f"  제안: {c.suggestion[:80]}")


def main(argv: list[str]) -> int:
    load_dotenv()
    use_llm = "--llm" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print("사용법: py -3 -m scripts.demo_verify [--llm] <pdf> [pdf ...]")
        return 1

    docs = load_package(paths)
    index = ArticleIndex.from_dir()
    llm = get_client() if use_llm else OfflineClient()
    print(f"LLM 모드: {llm.mode}\n")

    report = VerifyEngine(index, llm=llm).verify(docs)
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
