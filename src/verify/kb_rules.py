"""KB 판매서류 전용 검사 규칙.

범용 규칙(financial_rules)은 그대로 쓰고, KB 서식이라서 가능한 검사만 더한다.

KB는 위험등급이 두 곳에 있다.
  - 간이투자설명서: 운용사(KB자산운용 등)가 부여한 '투자위험등급'
  - 핵심[요약] 상품설명서: 판매사(국민은행)가 부여한 '당행부여 위험등급'
금융소비자 보호에 관한 감독규정 제12조 제2항 제3호는 판매사가 정한 위험등급이
발행인이 정한 것과 다르면 발행인과 적정성을 협의하도록 정한다.
두 등급이 어긋나면 협의 여부를 확인해야 하므로, 서류만으로 짚어줄 수 있다.

범용 도구는 두 문서를 모두 '상품설명서'로 뭉치므로 이 비교를 할 수 없다.
"""
from __future__ import annotations

from src.common.schemas import CheckStatus, ParsedDocument, RuleCheck
from src.parser.financial_extractor import field_map
from src.parser.kb_documents import FORM_BY_CODE, REQUIRED_FORMS, missing_forms
from src.verify.financial_rules import RuleLawHint, _risk_number

KB_LAW_HINTS: dict[str, RuleLawHint] = {
    "KB-GRADE": RuleLawHint(
        "판매업자가 정한 위험등급과 발행인 위험등급 상이 협의",
        preferred_articles=("12",),
        preferred_sources=("금융소비자 보호에 관한 감독규정",),
    ),
    "KB-DOC": RuleLawHint(
        "금융상품 판매 계약서류 제공의무 기록 유지 관리",
        preferred_articles=("23",),
        preferred_sources=("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정"),
    ),
}


def check_grade_agreement(forms: dict[str, ParsedDocument]) -> RuleCheck:
    """운용사 위험등급 ↔ 당행부여 위험등급 일치 여부(감독규정 12조)."""
    issuer = forms.get("kb_simple_prospectus")
    seller = forms.get("kb_key_summary")
    if issuer is None or seller is None:
        missing = []
        if issuer is None:
            missing.append("간이투자설명서")
        if seller is None:
            missing.append("핵심[요약] 상품설명서")
        return RuleCheck(
            rule_id="KB-GRADE",
            description="운용사·당행 위험등급 일치 여부",
            status=CheckStatus.MISSING,
            document_excerpt=f"{', '.join(missing)}가 없어 비교할 수 없습니다.",
            suggestion="두 서류를 함께 올리면 등급 상이 여부를 확인합니다.",
        )

    issuer_grade = field_map(issuer).get("product_risk_level")
    seller_grade = field_map(seller).get("product_risk_level")
    if not issuer_grade or not seller_grade:
        return RuleCheck(
            rule_id="KB-GRADE",
            description="운용사·당행 위험등급 일치 여부",
            status=CheckStatus.WARNING,
            document_excerpt=f"운용사 {issuer_grade or '미확인'} / 당행 {seller_grade or '미확인'}",
            suggestion="한쪽 등급을 읽지 못했습니다. 원본에서 직접 확인하세요.",
        )

    if _risk_number(issuer_grade) == _risk_number(seller_grade):
        return RuleCheck(
            rule_id="KB-GRADE",
            description="운용사·당행 위험등급 일치 여부",
            status=CheckStatus.PASS,
            document_excerpt=f"운용사·당행 모두 {issuer_grade}",
        )
    return RuleCheck(
        rule_id="KB-GRADE",
        description="운용사·당행 위험등급 일치 여부",
        status=CheckStatus.RISK,
        document_excerpt=f"운용사 {issuer_grade} / 당행 {seller_grade} — 상이",
        suggestion="판매사가 정한 위험등급이 발행인과 다릅니다. "
        "감독규정 제12조에 따라 발행인과 적정성을 협의했는지 확인하세요.",
    )


def check_kb_document_set(present_codes: set[str]) -> RuleCheck:
    """KB 판매서류 4종 구비 여부."""
    missing = missing_forms(present_codes)
    if not missing:
        return RuleCheck(
            rule_id="KB-DOC",
            description="KB 판매서류 4종 구비 여부",
            status=CheckStatus.PASS,
            document_excerpt=" · ".join(FORM_BY_CODE[c].label for c in REQUIRED_FORMS)
            + " 모두 확인",
        )
    labels = ", ".join(form.label for form in missing)
    return RuleCheck(
        rule_id="KB-DOC",
        description="KB 판매서류 4종 구비 여부",
        status=CheckStatus.MISSING,
        document_excerpt=f"누락 서류: {labels}",
        suggestion=f"{labels}를 업로드하세요. 서류가 빠지면 해당 항목은 검증할 수 없습니다.",
    )
