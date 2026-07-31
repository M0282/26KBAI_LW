"""모듈 간 인터페이스 계약 — 이 파일이 유일한 진실(single source of truth).

parser → verify → app 사이에 오가는 데이터 구조는 전부 여기에 정의한다.
스키마를 바꾸면 상대 모듈이 깨진다: 변경은 반드시 팀 상의 후 단독 PR로. (docs/PLAN.md 4장)
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ParsedField(BaseModel):
    """서류에서 추출한 개별 항목."""

    name: str = Field(description="항목명 (예: 사업자등록번호, 대표자명)")
    value: Optional[str] = Field(default=None, description="추출값. 못 찾으면 None")
    page: Optional[int] = Field(default=None, description="원본 문서에서의 페이지")
    confidence: Optional[float] = Field(default=None, description="추출 신뢰도 0~1")
    evidence_text: Optional[str] = Field(
        default=None,
        description="추출값을 뒷받침하는 원문 문구. PDF 좌표 탐색과 감사 근거에 사용",
    )


class ParsedDocument(BaseModel):
    """[parser 출력 → verify 입력] 파싱된 제출 서류."""

    document_id: str
    doc_type: str = Field(description="서류 유형 (예: 사업자등록증, 재무제표, 계약서)")
    fields: list[ParsedField]
    raw_text: str = Field(description="전체 원문 텍스트 (근거 하이라이트용)")


class CheckStatus(str, Enum):
    PASS = "pass"        # 통과
    MISSING = "missing"  # 필수 항목 누락
    RISK = "risk"        # 위험/독소 조항
    WARNING = "warning"  # 확인 권고


class EvidenceRef(BaseModel):
    """규칙 판정과 원문을 연결하는 구조화 근거 한 건.

    evidence_type:
      - field: 문서에서 추출된 필드 값
      - text: 문제가 된 실제 원문 문구
      - missing_field: 문서는 있으나 필수 필드를 찾지 못함
      - missing_document: 필수 문서가 없음
      - manual_review: 시스템이 자동 확인할 수 없어 사람이 확인해야 함
    """

    evidence_type: str
    document_id: Optional[str] = None
    field_name: Optional[str] = None
    value: Optional[str] = None
    excerpt: Optional[str] = None
    search_text: Optional[str] = Field(
        default=None, description="PDF 좌표 탐색에 사용할 원문 검색어"
    )
    page: Optional[int] = None


class RemediationAction(BaseModel):
    """판정 이후 담당자가 수행해야 할 공식 조치 계획."""

    required_action: str
    responsible_role: str
    sale_blocking: bool = Field(description="조치 완료 전 판매 진행을 차단해야 하는지")
    completion_criteria: str = Field(description="조치가 완료됐다고 판단할 수 있는 기준")


class RuleCheck(BaseModel):
    """개별 규칙 검증 결과 한 건."""

    rule_id: str
    description: str = Field(description="무엇을 검사했는지")
    status: CheckStatus
    evidence_clause: Optional[str] = Field(
        default=None, description="근거 조문 (예: 은행업감독규정 제78조 제1항)"
    )
    evidence_text: Optional[str] = Field(default=None, description="근거 조문 원문 발췌")
    evidence_items: list[EvidenceRef] = Field(
        default_factory=list,
        description="판정에 사용한 문서·필드·원문 또는 부재 근거 목록",
    )
    action_plan: Optional[RemediationAction] = Field(
        default=None, description="담당자·차단 여부·완료 기준을 포함한 공식 조치"
    )
    # 아래 두 필드는 기존 화면·내보내기 소비자와의 호환을 위해 유지한다.
    document_excerpt: Optional[str] = Field(
        default=None, description="문제가 된 서류 원문 발췌 (요약/하위 호환용)"
    )
    suggestion: Optional[str] = Field(default=None, description="수정/보완 제안 (하위 호환용)")


class VerificationReport(BaseModel):
    """[verify 출력 → app 입력] 서류 1건에 대한 최종 검증 리포트."""

    document_id: str
    doc_type: str
    checks: list[RuleCheck]
    summary: str = Field(description="LLM이 생성한 한 줄 요약")

    @property
    def has_blocker(self) -> bool:
        return any(c.status in (CheckStatus.MISSING, CheckStatus.RISK) for c in self.checks)
