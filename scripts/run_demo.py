"""데모 패키지를 앱과 동일한 경로로 검증·사전실행 (발표 사전캐싱용).

실행:
    py -3 -m scripts.make_demo_package   # 서류 생성(먼저)
    py -3 -m scripts.run_demo            # 오프라인 검증(무료)
    py -3 -m scripts.run_demo --llm      # 실제 LLM + 결과 캐시 예열(발표 전 1회)

--llm 으로 한 번 실행해두면 결과가 .cache/llm 에 저장되어, 발표 시연 때
같은 서류는 API 호출 0회·지연 0초로 재생된다(비용 규칙: 데모 사전캐싱).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from src.common.schemas import CheckStatus, ParsedDocument
from src.ingest.law_search import find_legal_basis
from src.parser.financial_extractor import extract_document
from src.parser.pdf_loader import load_pdf, to_parsed_document
from src.verify.ai_reasoner import build_legal_issues
from src.verify.financial_rules import LAW_HINTS, run_package_checks
from src.verify.metrics import compute_metrics

DEMO_DIR = Path("data/samples/demo")
ICON = {"risk": "[위험]", "missing": "[누락]", "warning": "[확인]", "pass": "[적합]"}


def main(argv: list[str]) -> int:
    load_dotenv()
    use_llm = "--llm" in argv
    pdfs = sorted(DEMO_DIR.glob("*.pdf"))
    if not pdfs:
        print("데모 서류가 없습니다. 먼저: py -3 -m scripts.make_demo_package")
        return 1

    started = time.perf_counter()
    docs: list[ParsedDocument] = []
    for path in pdfs:
        pdf = load_pdf(str(path))
        res = extract_document(to_parsed_document(pdf), use_llm=use_llm, locator=pdf.locate)
        docs.append(
            ParsedDocument(
                document_id=pdf.document_id,
                doc_type=res.doc_type,
                fields=res.fields,
                raw_text=pdf.text,
            )
        )

    checks = run_package_checks(docs)
    issues = build_legal_issues(docs, checks, use_llm=use_llm)
    metrics = compute_metrics(docs, checks, time.perf_counter() - started)

    print(f"=== 데모 검증 리포트 ({'LLM' if use_llm else '오프라인'}) — 서류 {len(docs)}종 ===")
    blockers = sum(c.status in (CheckStatus.RISK, CheckStatus.MISSING) for c in checks)
    print(f"차단성 이슈 {blockers}건 / 전체 검토 {len(checks)}건\n")
    for c in checks:
        if c.status == CheckStatus.PASS:
            continue
        iss = issues[c.rule_id]
        hint = LAW_HINTS[c.rule_id]
        laws = find_legal_basis(
            iss.search_query,
            preferred_articles=hint.preferred_articles,
            preferred_sources=hint.preferred_sources,
            top_k=1,
            allow_live=False,
        )
        cite = laws[0].citation if laws else "(로컬 조문 없음)"
        print(f"{ICON[c.status.value]} {c.rule_id}: {c.description}")
        if c.document_excerpt:
            print(f"   서류 근거: {c.document_excerpt[:70]}")
        print(f"   법령 근거: {cite}")

    print("\n=== 정량 지표 (ROI) ===")
    print("  " + metrics.summary_line())
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
