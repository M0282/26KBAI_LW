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


class RuleCheck(BaseModel):
    """개별 규칙 검증 결과 한 건."""

    rule_id: str
    description: str = Field(description="무엇을 검사했는지")
    status: CheckStatus
    evidence_clause: Optional[str] = Field(
        default=None, description="근거 조문 (예: 은행업감독규정 제78조 제1항)"
    )
    evidence_text: Optional[str] = Field(default=None, description="근거 조문 원문 발췌")
    document_excerpt: Optional[str] = Field(
        default=None, description="문제가 된 서류 원문 발췌 (하이라이트용)"
    )
    suggestion: Optional[str] = Field(default=None, description="수정/보완 제안")


class VerificationReport(BaseModel):
    """[verify 출력 → app 입력] 서류 1건에 대한 최종 검증 리포트."""

    document_id: str
    doc_type: str
    checks: list[RuleCheck]
    summary: str = Field(description="LLM이 생성한 한 줄 요약")

    @property
    def has_blocker(self) -> bool:
        return any(c.status in (CheckStatus.MISSING, CheckStatus.RISK) for c in self.checks)
