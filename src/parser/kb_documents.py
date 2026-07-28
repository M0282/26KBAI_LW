"""KB 펀드 판매서류 전용 서식 인식.

범용 분류기는 문서를 4종(적합성진단표·상품설명서·가입신청서·설명확인서)으로 나누는데,
KB가 실제로 교부하는 서류와 어긋난다(실측):
  - 상품설명서가 2종이다 — 운용사가 만든 '간이투자설명서'와
    판매사가 만든 '핵심[요약] 상품설명서'. 둘을 하나로 뭉치면 화면에
    '상품설명서'가 두 개 뜨고, 어느 쪽 위험등급인지 구분되지 않는다.
  - '설명확인서'가 별도 파일로 없다. 확인 문구와 서명이 계약서에 통합돼 있다.

KB 서식은 고정 양식이라 문서마다 고유 문구가 있다. 실측으로 확인한 시그니처만으로
LLM 없이 100% 구분된다 — 분류 단계에서 모델 변덕을 아예 없앨 수 있다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KbForm:
    """KB 서식 한 종류."""

    code: str
    label: str
    canonical: str          # 판정 규칙이 쓰는 표준 문서유형
    signatures: tuple[str, ...]   # 이 서식에만 나오는 문구(공백 무시 비교)
    role: str               # 화면에 표시할 역할 설명


KB_FORMS: tuple[KbForm, ...] = (
    KbForm(
        code="kb_suitability",
        label="적합성 진단표",
        canonical="suitability_form",
        signatures=("투자성향분석", "투자성향안내", "투자성향은"),
        role="고객 투자성향 — 어느 위험등급까지 가입 가능한지",
    ),
    KbForm(
        code="kb_simple_prospectus",
        label="간이투자설명서",
        canonical="product_description",
        signatures=("간이투자설명서", "펀드코드", "증권신고서"),
        role="운용사 발행 — 상품코드·투자위험등급의 원천",
    ),
    KbForm(
        code="kb_key_summary",
        label="핵심[요약] 상품설명서",
        canonical="product_description",
        signatures=("핵심[요약]", "당행부여위험등급", "고객교부용"),
        role="판매사 발행 고객교부용 — 당행부여 위험등급",
    ),
    KbForm(
        code="kb_contract",
        label="집합투자증권 계약서",
        canonical="application",
        signatures=("저축자", "집합투자상품통장"),
        role="계약일·서명 — 설명확인이 통합된 양식",
    ),
)

FORM_BY_CODE = {form.code: form for form in KB_FORMS}
# 적합성 진단표는 고객 단위 서류라 판매 건마다 하나씩 필요하다.
REQUIRED_FORMS = tuple(form.code for form in KB_FORMS)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def classify_kb_form(text: str) -> str | None:
    """KB 서식 코드를 돌려준다. 어느 서식에도 해당하지 않으면 None.

    시그니처를 많이 맞힌 서식을 고른다. 동점이면 판단하지 않는다(추측하지 않음).
    """
    compact = _compact(text)
    scores = {
        form.code: sum(1 for s in form.signatures if _compact(s) in compact)
        for form in KB_FORMS
    }
    best = max(scores, key=lambda c: scores[c])
    if scores[best] == 0:
        return None
    ties = [c for c, n in scores.items() if n == scores[best]]
    return best if len(ties) == 1 else None


def missing_forms(present_codes: set[str]) -> list[KbForm]:
    """구비되지 않은 KB 서식 목록."""
    return [FORM_BY_CODE[c] for c in REQUIRED_FORMS if c not in present_codes]
