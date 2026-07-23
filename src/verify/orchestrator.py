"""규정 개정 자동 재검증 오케스트레이션.

한 번의 호출로: 개정 감지 → 개정 규정 자동 재수집 → 재검증 → 판정 diff.
배치/스케줄러에서 주기 실행하면 "규정이 바뀌면 저장된 서류가 자동 재평가"된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.common.schemas import ParsedDocument, RuleCheck
from src.ingest.regulation_version import (
    UpdateStatus,
    check_live_updates,
    load_snapshot,
    save_snapshot,
    snapshot_current,
)
from src.verify.financial_rules import run_package_checks
from src.verify.reverify import ReverifyDiff, diff_checks


@dataclass
class ReverificationReport:
    ran_at: str
    changed_regulations: list[UpdateStatus] = field(default_factory=list)
    refetched: list[str] = field(default_factory=list)
    diff: ReverifyDiff | None = None
    new_checks: list[RuleCheck] = field(default_factory=list)
    note: str = ""

    @property
    def reverified(self) -> bool:
        return self.diff is not None

    def summary_line(self) -> str:
        if self.changed_regulations:
            regs = ", ".join(u.source for u in self.changed_regulations)
            head = f"규정 개정 감지({regs}) → 재수집 {len(self.refetched)}건 → 재검증"
        else:
            head = "규정 최신(개정 없음) → 재검증"
        tail = self.diff.summary_line() if self.diff else "판정 없음"
        return f"{head}: {tail}"


def auto_reverify(
    documents: list[ParsedDocument],
    baseline_checks: list[RuleCheck],
    *,
    refetch: bool = True,
    check_live: bool = True,
    policy: dict[str, int] | None = None,
) -> ReverificationReport:
    """개정 감지→(자동 재수집)→재검증→diff 를 한 번에 수행.

    - baseline_checks: 개정 전(직전) 검증 결과. 이것과 재검증 결과를 diff 한다.
    - refetch: 개정 감지 시 해당 규정을 자동 재수집할지.
    - check_live: 국가법령정보 API로 현행 시행일자 대조(키 필요). 실패해도 재검증은 진행.
    - policy: 재검증에 적용할 적합성 매트릭스(규정 파라미터). None이면 현행.
    """
    report = ReverificationReport(ran_at=datetime.now().isoformat(timespec="seconds"))

    # 1) 개정 감지
    if check_live:
        try:
            snapshot = load_snapshot() or snapshot_current()
            report.changed_regulations = [u for u in check_live_updates(snapshot) if u.changed]
        except Exception as exc:  # API 실패는 치명적이지 않음 — 재검증은 계속
            report.note = f"개정 감지 생략(사유: {type(exc).__name__})"

    # 2) 개정된 규정 자동 재수집 + 스냅샷 갱신
    if refetch and report.changed_regulations:
        from src.ingest.fetch_regulations import refetch_source

        for u in report.changed_regulations:
            try:
                if refetch_source(u.source) >= 0:
                    report.refetched.append(u.source)
            except Exception:
                pass
        try:
            save_snapshot(snapshot_current())
        except Exception:
            pass

    # 3) 재검증 + 4) diff
    report.new_checks = run_package_checks(documents, profile_min_grade=policy)
    report.diff = diff_checks(baseline_checks, report.new_checks)
    return report
