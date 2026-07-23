"""정량 지표 — 심사 어필용 ROI 측정 (검증 시간·적발 건수·시간 단축률).

발표에서 "행원 수작업 대비 얼마나 빠르고 무엇을 잡았는가"를 숫자로 보여준다.
수작업 기준시간은 추정치이며, 발표 시 근거(가정)를 함께 명시할 것.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from src.common.schemas import CheckStatus, ParsedDocument, RuleCheck

# 행원이 판매서류 1종을 규정과 수작업 대조하는 데 걸리는 추정 시간(분).
# 보수적 가정: 조문 확인·교차 대조·설명 이행 점검 포함. 발표 시 가정으로 명시.
DEFAULT_MANUAL_MINUTES_PER_DOC = 15.0


@dataclass
class VerificationMetrics:
    document_count: int
    check_count: int
    blocker_count: int          # 위험 + 누락 (즉시 조치 필요)
    warning_count: int          # 확인 권고
    passed_count: int
    elapsed_seconds: float       # AI 검증 실측 소요
    manual_baseline_seconds: float  # 수작업 추정 소요

    @property
    def time_saved_seconds(self) -> float:
        return max(0.0, self.manual_baseline_seconds - self.elapsed_seconds)

    @property
    def time_saved_ratio(self) -> float:
        if self.manual_baseline_seconds <= 0:
            return 0.0
        return self.time_saved_seconds / self.manual_baseline_seconds

    @property
    def detection_count(self) -> int:
        """적발 건수(통과 제외 = 조치·확인이 필요한 항목)."""
        return self.blocker_count + self.warning_count

    def as_dict(self) -> dict:
        d = asdict(self)
        d["time_saved_seconds"] = round(self.time_saved_seconds, 1)
        d["time_saved_ratio"] = round(self.time_saved_ratio, 3)
        d["detection_count"] = self.detection_count
        return d

    def summary_line(self) -> str:
        pct = round(self.time_saved_ratio * 100)
        return (
            f"서류 {self.document_count}종 검증 {self.elapsed_seconds:.1f}초 "
            f"(수작업 추정 {self.manual_baseline_seconds/60:.0f}분 → {pct}% 단축), "
            f"적발 {self.detection_count}건(위험·누락 {self.blocker_count})"
        )


def compute_metrics(
    documents: list[ParsedDocument],
    checks: list[RuleCheck],
    elapsed_seconds: float,
    manual_minutes_per_doc: float = DEFAULT_MANUAL_MINUTES_PER_DOC,
) -> VerificationMetrics:
    blocker = sum(c.status in (CheckStatus.RISK, CheckStatus.MISSING) for c in checks)
    warning = sum(c.status == CheckStatus.WARNING for c in checks)
    passed = sum(c.status == CheckStatus.PASS for c in checks)
    return VerificationMetrics(
        document_count=len(documents),
        check_count=len(checks),
        blocker_count=blocker,
        warning_count=warning,
        passed_count=passed,
        elapsed_seconds=round(elapsed_seconds, 2),
        manual_baseline_seconds=len(documents) * manual_minutes_per_doc * 60,
    )
