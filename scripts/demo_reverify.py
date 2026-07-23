"""규정 개정 재검증 데모.

1) 현재 수집한 규정의 버전(시행일자) 표시
2) 국가법령정보 API로 현행 시행일자와 대조 → 개정 감지 (--live, LAW_API_OC 필요)
3) 규정 개정(적합성 등급 매트릭스 강화)을 적용해 동일 서류를 재검증하고,
   개정 전/후 판정 diff를 표시 → "규정이 바뀌니 새 위반이 발생"

실행:
    py -3 -m scripts.make_demo_package          # 데모 서류(먼저)
    py -3 -m scripts.demo_reverify              # 버전 + 재검증 diff
    py -3 -m scripts.demo_reverify --live       # + 국가법령정보 현행 대조
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

from src.common.schemas import ParsedDocument, ParsedField
from src.ingest.regulation_version import (
    check_live_updates,
    snapshot_current,
)
from src.verify.financial_rules import DEFAULT_PROFILE_MIN_ALLOWED_GRADE, run_package_checks
from src.verify.reverify import diff_checks


def main(argv: list[str]) -> int:
    load_dotenv()
    live = "--live" in argv

    print("=== 1) 현재 규정 버전 (시행일자) ===")
    versions = snapshot_current()
    if not versions:
        print("  규정 데이터가 없습니다. py -3 -m src.ingest.fetch_regulations 먼저 실행")
        return 1
    for v in versions.values():
        print(f"  {v.source}: 시행 {v.enforcement_date or '미상'} ({v.article_count}개 조문)")

    if live:
        print("\n=== 2) 국가법령정보 현행본 대조 (개정 감지) ===")
        for s in check_live_updates(versions):
            mark = "⚠️ 개정 감지" if s.changed else "최신"
            print(f"  {s.source}: 스냅샷 {s.stored_date} / 현행 {s.live_date or '조회실패'} → {mark}")

    print("\n=== 3) 규정 개정 재검증 (적합성 등급 매트릭스 강화 시뮬레이션) ===")
    # 경계 사례: 위험중립형 고객 ↔ 4등급 상품.
    #  개정 전(위험중립형 허용 4등급): 적합(PASS)
    #  개정 후(강화, 허용 5등급): 4등급은 부적합 → 신규 위반(RISK)
    borderline = [
        ParsedDocument(
            document_id="적합성진단표(경계)", doc_type="suitability_form",
            fields=[ParsedField(name="customer_profile", value="위험중립형", page=1)],
            raw_text="투자성향: 위험중립형",
        ),
        ParsedDocument(
            document_id="상품설명서(경계)", doc_type="product_description",
            fields=[ParsedField(name="product_risk_level", value="4등급", page=1)],
            raw_text="위험등급: 4등급",
        ),
    ]
    print("  경계 서류: 위험중립형 고객 ↔ 4등급 상품")

    # 개정 전: 현행 매트릭스
    before = run_package_checks(borderline)
    # 개정 후: 위험중립형 허용 등급을 한 단계 강화(더 낮은 위험만 허용)
    amended = dict(DEFAULT_PROFILE_MIN_ALLOWED_GRADE)
    amended["위험중립형"] = amended["위험중립형"] + 1
    after = run_package_checks(borderline, profile_min_grade=amended)

    diff = diff_checks(before, after)
    print("  " + diff.summary_line())
    for c in diff.added:
        print(f"    + 신규 위반 [{c.status.value}] {c.rule_id}: {c.document_excerpt}")
    for c in diff.resolved:
        print(f"    - 해소 [{c.status.value}] {c.rule_id}")
    if not diff.has_change:
        print("    (이 데모 서류는 강화된 등급 매트릭스에서도 판정이 동일합니다)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
