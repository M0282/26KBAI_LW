"""금융상품 판매서류 패키지 결정론적 검증 규칙.

LLM은 문서 이해와 법적 쟁점 설명을 돕지만, 아래 규칙의 상태값을 임의로 변경하지 않는다.
투자성향-위험등급 매트릭스는 MVP 데모 정책이며 실제 적용 전 KB 내부 기준으로 교체해야 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.common.schemas import CheckStatus, ParsedDocument, RuleCheck
from src.parser.financial_extractor import field_map, parse_iso_date


@dataclass(frozen=True)
class RuleLawHint:
    query: str
    preferred_articles: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()


LAW_HINTS: dict[str, RuleLawHint] = {
    "PKG-001": RuleLawHint(
        "금융상품 판매 서류 상품 동일성 설명 확인",
        preferred_sources=("금융소비자 보호에 관한 법률",),
    ),
    "FIT-001": RuleLawHint(
        "일반금융소비자 투자성향 고위험 금융상품 적합성 원칙",
        preferred_articles=("17",),
        preferred_sources=("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정"),
    ),
    "EXP-001": RuleLawHint(
        "금융상품 중요사항 설명의무 원금손실 수수료 위험",
        preferred_articles=("19",),
        preferred_sources=("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정"),
    ),
    "DATE-001": RuleLawHint(
        "금융상품 계약 체결 전 설명의무 설명 시점",
        preferred_articles=("19",),
        preferred_sources=("금융소비자 보호에 관한 법률",),
    ),
    "ACK-001": RuleLawHint(
        "금융상품 설명 확인 증빙 서명 교부",
        preferred_articles=("19",),
        preferred_sources=("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정"),
    ),
}

# 숫자가 작을수록 위험도가 높다. 값은 대회 MVP용 예시이며 실제 은행 정책으로 교체한다.
DEFAULT_PROFILE_MIN_ALLOWED_GRADE = {
    "안정형": 6,
    "안정추구형": 5,
    "위험중립형": 4,
    "적극투자형": 3,
    "공격투자형": 1,
}


def _documents_with(documents: Iterable[ParsedDocument], field_name: str) -> list[tuple[ParsedDocument, str]]:
    return [
        (document, value)
        for document in documents
        if (value := field_map(document).get(field_name))
    ]


def _risk_number(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"([1-6])\s*등급", value)
    return int(match.group(1)) if match else None


def _excerpt(document: ParsedDocument, value: str | None) -> str | None:
    if not value:
        return None
    compact_value = re.sub(r"\s+", "", value)
    compact_text = re.sub(r"\s+", "", document.raw_text)
    compact_index = compact_text.find(compact_value)
    if compact_index < 0:
        return value
    # 원문 인덱스 역산은 불안정하므로, 주변 문장 대신 검증된 값 자체를 표시한다.
    return value


def _identity_key(value: str) -> str:
    value = re.sub(r"\s+", "", value).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", value)


def check_product_identity(documents: list[ParsedDocument]) -> RuleCheck:
    codes = _documents_with(documents, "product_code")
    names = _documents_with(documents, "product_name")
    selected = codes if len(codes) >= 2 else names
    values = [value for _, value in selected]
    unique = {_identity_key(value) for value in values}

    if len(values) < 2:
        return RuleCheck(
            rule_id="PKG-001",
            description="문서 간 상품명·상품코드 일치 여부",
            status=CheckStatus.WARNING,
            document_excerpt="비교 가능한 상품 식별값이 2개 미만입니다.",
            suggestion="각 문서에서 상품명 또는 상품코드를 확인하세요.",
        )
    if len(unique) == 1:
        return RuleCheck(
            rule_id="PKG-001",
            description="문서 간 상품명·상품코드 일치 여부",
            status=CheckStatus.PASS,
            document_excerpt=f"공통 상품 식별값: {values[0]}",
        )
    return RuleCheck(
        rule_id="PKG-001",
        description="문서 간 상품명·상품코드 일치 여부",
        status=CheckStatus.RISK,
        document_excerpt=" / ".join(values),
        suggestion="서류 패키지에 서로 다른 상품이 혼입됐는지 확인하세요.",
    )


def check_suitability(
    documents: list[ParsedDocument],
    profile_min_grade: dict[str, int] | None = None,
) -> RuleCheck:
    profiles = _documents_with(documents, "customer_profile")
    risks = _documents_with(documents, "product_risk_level")
    if not profiles or not risks:
        return RuleCheck(
            rule_id="FIT-001",
            description="고객 투자성향과 상품 위험등급 교차 검증",
            status=CheckStatus.MISSING,
            document_excerpt="투자성향 또는 상품 위험등급을 찾지 못했습니다.",
            suggestion="적합성 진단표와 상품설명서를 확인하세요.",
        )

    profile_doc, profile = profiles[0]
    risk_doc, risk = risks[0]
    risk_no = _risk_number(risk)
    threshold = (profile_min_grade or DEFAULT_PROFILE_MIN_ALLOWED_GRADE).get(profile)
    if risk_no is None or threshold is None:
        return RuleCheck(
            rule_id="FIT-001",
            description="고객 투자성향과 상품 위험등급 교차 검증",
            status=CheckStatus.WARNING,
            document_excerpt=f"투자성향 {profile}, 상품 위험등급 {risk}",
            suggestion="내부 적합성 등급 매트릭스에서 허용 여부를 확인하세요.",
        )

    # 예: 안정형 threshold=6. 1~5등급은 6등급보다 위험하므로 경고한다.
    if risk_no < threshold:
        return RuleCheck(
            rule_id="FIT-001",
            description="고객 투자성향과 상품 위험등급 교차 검증",
            status=CheckStatus.RISK,
            document_excerpt=(
                f"{profile_doc.document_id}: {_excerpt(profile_doc, profile)} | "
                f"{risk_doc.document_id}: {_excerpt(risk_doc, risk)}"
            ),
            suggestion="적합성 판단 및 부적합 상품 거래 확인 절차 수행 여부를 추가 확인하세요.",
        )
    return RuleCheck(
        rule_id="FIT-001",
        description="고객 투자성향과 상품 위험등급 교차 검증",
        status=CheckStatus.PASS,
        document_excerpt=f"투자성향 {profile}, 상품 위험등급 {risk}",
    )


def check_explanation(document: ParsedDocument) -> RuleCheck:
    values = field_map(document)
    semantic = {
        "원금손실": values.get("principal_loss_explained"),
        "위험등급": values.get("risk_level_explained"),
        "수수료·비용": values.get("fees_explained"),
    }
    missing = [name for name, value in semantic.items() if not value]
    if missing:
        return RuleCheck(
            rule_id="EXP-001",
            description="상품 중요사항 설명 존재 여부",
            status=CheckStatus.MISSING,
            document_excerpt=f"미확인 항목: {', '.join(missing)}",
            suggestion="상품 유형에 맞는 핵심 위험·비용 설명이 실제 문서에 있는지 보완하세요.",
        )
    return RuleCheck(
        rule_id="EXP-001",
        description="상품 중요사항 설명 존재 여부",
        status=CheckStatus.PASS,
        document_excerpt="원금손실·위험등급·수수료 관련 설명 확인",
    )


def check_dates(documents: list[ParsedDocument]) -> RuleCheck:
    explanation = _documents_with(documents, "explanation_date")
    contract = _documents_with(documents, "contract_date")
    if not explanation or not contract:
        return RuleCheck(
            rule_id="DATE-001",
            description="설명일과 계약일의 선후관계",
            status=CheckStatus.WARNING,
            document_excerpt="설명일 또는 계약일을 찾지 못했습니다.",
            suggestion="계약 전에 설명이 이뤄졌는지 날짜를 확인하세요.",
        )
    _, explanation_value = explanation[0]
    _, contract_value = contract[0]
    explanation_date = parse_iso_date(explanation_value)
    contract_date = parse_iso_date(contract_value)
    if not explanation_date or not contract_date:
        return RuleCheck(
            rule_id="DATE-001",
            description="설명일과 계약일의 선후관계",
            status=CheckStatus.WARNING,
            document_excerpt=f"설명일 {explanation_value} / 계약일 {contract_value}",
            suggestion="날짜 형식을 확인하세요.",
        )
    if explanation_date > contract_date:
        return RuleCheck(
            rule_id="DATE-001",
            description="설명일과 계약일의 선후관계",
            status=CheckStatus.RISK,
            document_excerpt=f"설명일 {explanation_value} / 계약일 {contract_value}",
            suggestion="계약 이후 설명으로 기록된 사유와 실제 설명 시점을 확인하세요.",
        )
    return RuleCheck(
        rule_id="DATE-001",
        description="설명일과 계약일의 선후관계",
        status=CheckStatus.PASS,
        document_excerpt=f"설명일 {explanation_value} / 계약일 {contract_value}",
    )


def check_acknowledgement(documents: list[ParsedDocument]) -> RuleCheck:
    acknowledgements = _documents_with(documents, "customer_acknowledgement")
    staff = _documents_with(documents, "staff_name")
    if not acknowledgements:
        return RuleCheck(
            rule_id="ACK-001",
            description="고객 설명 확인 증빙",
            status=CheckStatus.MISSING,
            document_excerpt="고객 확인값을 찾지 못했습니다.",
            suggestion="설명 확인서의 고객 확인·서명란을 확인하세요.",
        )
    value = acknowledgements[0][1]
    negative = any(token in value.replace(" ", "") for token in ("미확인", "없음", "미서명", "아니오"))
    if negative:
        return RuleCheck(
            rule_id="ACK-001",
            description="고객 설명 확인 증빙",
            status=CheckStatus.RISK,
            document_excerpt=f"고객 확인: {value}",
            suggestion="고객 확인 또는 서명 증빙을 보완하세요.",
        )
    status = CheckStatus.PASS if staff else CheckStatus.WARNING
    return RuleCheck(
        rule_id="ACK-001",
        description="고객 설명 확인 증빙",
        status=status,
        document_excerpt=f"고객 확인: {value}" + (f" / 담당자: {staff[0][1]}" if staff else ""),
        suggestion=None if staff else "설명 담당자 정보도 함께 확인하세요.",
    )


def run_package_checks(documents: list[ParsedDocument]) -> list[RuleCheck]:
    checks = [
        check_product_identity(documents),
        check_suitability(documents),
        check_dates(documents),
        check_acknowledgement(documents),
    ]
    product_documents = [document for document in documents if document.doc_type == "product_description"]
    checks.append(
        check_explanation(product_documents[0])
        if product_documents
        else RuleCheck(
            rule_id="EXP-001",
            description="상품 중요사항 설명 존재 여부",
            status=CheckStatus.MISSING,
            document_excerpt="상품설명서로 분류된 문서가 없습니다.",
            suggestion="상품설명서를 업로드하세요.",
        )
    )
    return checks
