"""금융상품 판매서류 패키지 결정론적 검증 규칙.

LLM은 문서 이해와 법적 쟁점 설명을 돕지만, 아래 규칙의 상태값을 임의로 변경하지 않는다.
투자성향-위험등급 매트릭스는 MVP 데모 정책이며 실제 적용 전 KB 내부 기준으로 교체해야 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from src.common.schemas import CheckStatus, ParsedDocument, RuleCheck
from src.parser.financial_extractor import field_map, parse_iso_date, states_unsigned


@dataclass(frozen=True)
class RuleLawHint:
    query: str
    preferred_articles: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()


LAW_HINTS: dict[str, RuleLawHint] = {
    "PKG-001": RuleLawHint(
        "금융상품 판매 서류 상품 동일성 설명 확인",
        # 조문 힌트가 없으면 BM25가 정의·유형 조문(제2·3·4조)을 상위로 올려 근거가 겉돈다.
        preferred_articles=("19",),
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
    "ADV-001": RuleLawHint(
        "투자성 상품 부당권유 금지 단정적 판단 원금보장 표현",
        preferred_articles=("21",),
        preferred_sources=("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정"),
    ),
    "DOC-001": RuleLawHint(
        "금융상품 판매 계약서류 제공의무 기록 유지 관리",
        preferred_articles=("23",),
        preferred_sources=("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정"),
    ),
    "REC-001": RuleLawHint(
        # 녹취 의무의 직접 근거는 자본시장법 시행령·금융투자업규정이라 현재 코퍼스에 없다.
        # 가장 근접한 금소법 28조(자료의 기록 및 유지·관리)를 근거로 제시한다.
        "금융상품 판매 과정 자료의 기록 유지 관리 고령투자자 보호",
        preferred_articles=("28",),
        preferred_sources=("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정"),
    ),
}

# 금소법 21조: 투자성 상품에 '손실이 없다'는 단정적 판단을 제공하는 것은 금지된다.
# 다만 실물 서류에는 같은 낱말이 정반대 맥락으로 흔하게 등장한다(실측):
#   - "원금이 보장되지 않으며 전부 손실될 수 있습니다"  → 올바른 고지
#   - "원금보장추구형 구조화 상품"                      → 상품 유형 명칭
# 그래서 낱말만 보면 오탐 100%다. 부정 표현과 유형 명칭을 먼저 걷어낸다.
_GUARANTEE_CLAIMS = (
    r"원금[이은을]?보장",
    r"원금[이은을]?보전",
    r"확정수익",
    r"수익[을이]?보장",
    r"손실[이은]?없",
    r"절대안전",
    r"반드시수익",
)
# 뒤에 이런 표현이 붙으면 위반이 아니다(부정 고지 또는 상품 유형명).
_CLAIM_EXCEPTIONS = (
    "되지", "되지않", "않", "아닙", "아니", "없는", "불가", "추구", "형", "제외",
)
# '원금보장 여부'는 중립적 질의 표현이지 보장 약속이 아니다. 그런데 실물 서류에서는
# 이 낱말이 통째로 쪼개진다 — 신한 ELS 핵심설명서 실측:
#
#     …발행조건에 따른 원금보장여
#     - 168 -
#     투자자
#     유의사항
#     부와 관계없이 시장상황에 따라 원금손실이 발생할 수 있습니다.
#
# 쪽번호는 _PAGE_ARTIFACT 가 지우지만 여백 라벨('투자자 유의사항')은 남아서
# '여'와 '부' 사이에 끼어든다. 그래서 '여부'를 붙어 있는 낱말로만 찾으면
# 이 정상 위험고지가 부당권유로 잡힌다(오탐).
#
# 반대로 '여' 한 글자만으로 예외 처리하면 '하여·위하여·관하여'를 전부 삼켜
# "수익을 보장하여 드립니다" 같은 명백한 위반을 놓친다(미탐).
#
# 그래서 '여 … 부'를 짧은 거리 안에서 함께 볼 때만 여부로 인정한다.
_CLAIM_YEOBU = re.compile(r"여.{0,10}?부")
_EXCEPTION_WINDOW = 12  # 표현 직후 이 글자 수 안에 예외어가 있으면 정상으로 본다


_PAGE_ARTIFACT = re.compile(r"-\s*\d{1,4}\s*-")  # 쪽번호가 낱말 중간에 끼어든다


def find_guarantee_claims(text: str) -> list[str]:
    """부당권유 소지가 있는 단정적 표현을 찾는다(부정 고지·유형명은 제외)."""
    # 실측: "원금보장여-168-부와 관계없이"처럼 쪽번호가 낱말을 쪼개 예외 판정을 방해한다.
    compact = re.sub(r"\s+", "", _PAGE_ARTIFACT.sub("", text))
    found: list[str] = []
    for pattern in _GUARANTEE_CLAIMS:
        for match in re.finditer(pattern, compact):
            tail = compact[match.end() : match.end() + _EXCEPTION_WINDOW]
            if any(token in tail for token in _CLAIM_EXCEPTIONS):
                continue  # "원금보장되지 않습니다" / "원금보장추구형" → 정상
            if _CLAIM_YEOBU.search(tail):
                continue  # "원금보장 여부" (여백 라벨이 낱말을 쪼갠 경우 포함) → 정상
            start = max(0, match.start() - 20)
            found.append(compact[start : match.end() + 20])
    return found

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


# 펀드 클래스 표기: "…증권 투자신탁(채권혼합) C-E", "… (주식) A", "… 종류C" 등.
# 같은 펀드의 클래스 차이(수수료 구조)일 뿐 다른 상품이 아니다.
_CLASS_SUFFIX = re.compile(r"\)\s*(?:종류형?\s*)?[A-Za-z][A-Za-z0-9\-]{0,5}\s*$")


def _base_identity_key(value: str) -> str:
    """클래스 접미사를 떼어낸 펀드 기본명 키."""
    return _identity_key(_CLASS_SUFFIX.sub(")", value.strip()))


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
    # 클래스 접미사만 다르면 같은 펀드다 → 다른 상품 혼입(위험)이 아니라 표기 확인(주의).
    if len({_base_identity_key(value) for value in values}) == 1:
        return RuleCheck(
            rule_id="PKG-001",
            description="문서 간 상품명·상품코드 일치 여부",
            status=CheckStatus.WARNING,
            document_excerpt=" / ".join(values),
            suggestion="같은 펀드의 클래스(수수료 유형) 표기 차이로 보입니다. "
            "판매 클래스가 서류 간 일치하는지 확인하세요.",
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

    table = profile_min_grade or DEFAULT_PROFILE_MIN_ALLOWED_GRADE
    # 패키지에 상품설명서가 여럿이면 등급이 서로 다를 수 있다. 첫 문서를 고르면
    # 업로드 순서만으로 판정이 갈리므로, 판단은 늘 **가장 보수적인 값**으로 한다.
    # - 상품: 가장 위험한 등급(숫자가 작은 쪽)
    # - 고객: 위험 감내도가 가장 낮은 성향(허용 최소등급이 큰 쪽)
    risk_doc, risk = min(
        risks, key=lambda pair: (_risk_number(pair[1]) or 99, pair[0].document_id)
    )
    profile_doc, profile = max(
        profiles, key=lambda pair: (table.get(pair[1], 0), pair[0].document_id)
    )
    risk_no = _risk_number(risk)
    threshold = table.get(profile)
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


def _missing_explanations(document: ParsedDocument) -> list[str]:
    """문서에서 빠진 중요사항 설명 항목명."""
    values = field_map(document)
    semantic = {
        "원금손실": values.get("principal_loss_explained"),
        "위험등급": values.get("risk_level_explained"),
        "수수료·비용": values.get("fees_explained"),
    }
    return [name for name, value in semantic.items() if not value]


def check_explanations(documents: list[ParsedDocument]) -> RuleCheck:
    """패키지 안의 **모든** 상품설명서를 검사한다.

    기존에는 product_documents[0], 즉 첫 문서만 봤다. 그래서 같은 서류 묶음이라도
    업로드 순서에 따라 통과/누락이 갈렸고(실측), 두 번째 이후 상품설명서에 설명이
    빠져 있어도 드러나지 않았다. 문서 ID로 정렬해 메시지까지 순서에 무관하게 만든다.
    """
    products = [d for d in documents if d.doc_type == "product_description"]
    if not products:
        return RuleCheck(
            rule_id="EXP-001",
            description="상품 중요사항 설명 존재 여부",
            status=CheckStatus.MISSING,
            document_excerpt="상품설명서로 분류된 문서가 없습니다.",
            suggestion="상품설명서를 업로드하세요.",
        )
    gaps = [
        (document.document_id, missing)
        for document in sorted(products, key=lambda d: d.document_id)
        if (missing := _missing_explanations(document))
    ]
    if gaps:
        return RuleCheck(
            rule_id="EXP-001",
            description="상품 중요사항 설명 존재 여부",
            status=CheckStatus.MISSING,
            document_excerpt=" / ".join(
                f"{document_id}: {', '.join(missing)} 미확인" for document_id, missing in gaps
            ),
            suggestion="상품 유형에 맞는 핵심 위험·비용 설명이 실제 문서에 있는지 보완하세요.",
        )
    return RuleCheck(
        rule_id="EXP-001",
        description="상품 중요사항 설명 존재 여부",
        status=CheckStatus.PASS,
        document_excerpt=f"상품설명서 {len(products)}건 모두 원금손실·위험등급·수수료 설명 확인",
    )


# 21조는 '판매자가 고객에게 한 표현'을 규율한다. 적합성 진단표의 투자목적은
# 고객이 원하는 바를 적은 것이라(실측: "투자목적: 원금보전 및 예금수준 안정수익")
# 검사 대상이 아니다.
_ADVICE_DOC_TYPES = ("product_description", "application", "acknowledgement")


def check_unfair_solicitation(documents: list[ParsedDocument]) -> RuleCheck:
    """부당권유 금지 — 원금보장·확정수익 등 단정적 표현이 있는지(금소법 21조)."""
    targets = [d for d in documents if d.doc_type in _ADVICE_DOC_TYPES]
    if not targets:
        return RuleCheck(
            rule_id="ADV-001",
            description="부당권유 금지 표현 검사",
            status=CheckStatus.WARNING,
            document_excerpt="검사할 판매 서류가 없습니다.",
            suggestion="상품설명서·가입신청서를 업로드하세요.",
        )
    hits = [
        (document.document_id, claim)
        for document in sorted(targets, key=lambda d: d.document_id)
        for claim in find_guarantee_claims(document.raw_text)
    ]
    if hits:
        return RuleCheck(
            rule_id="ADV-001",
            description="부당권유 금지 표현 검사",
            status=CheckStatus.RISK,
            document_excerpt=" / ".join(f"{name}: …{claim}…" for name, claim in hits[:3]),
            suggestion="원금·수익을 보장하는 단정적 표현은 투자성 상품에 사용할 수 없습니다. "
            "해당 문구의 사용 경위와 정정 여부를 확인하세요.",
        )
    return RuleCheck(
        rule_id="ADV-001",
        description="부당권유 금지 표현 검사",
        status=CheckStatus.PASS,
        document_excerpt=f"판매 서류 {len(targets)}건에서 원금보장·확정수익 등 단정적 표현 없음",
    )


# 판매 시 갖춰야 할 서류 4종. 하나라도 없으면 교차 검증 자체가 불완전해진다.
REQUIRED_DOC_TYPES = ("suitability_form", "product_description", "application", "acknowledgement")
REQUIRED_DOC_LABELS = {
    "suitability_form": "적합성 진단표",
    "product_description": "상품설명서",
    "application": "가입신청서",
    "acknowledgement": "설명 확인서",
}


# 비대면(모바일) 판매에서는 설명확인서가 별도 파일로 존재하지 않고 가입신청서 안의
# 확인 문구 + 전자서명으로 대체된다. 실측 — 집합투자증권 계약서에 다음이 들어 있다:
#   "위 계약내용에 대해 모두 확인하였으며, 주요내용을 충분히 설명듣고 이해하였습니다"
# 이때 별도 서류가 없다는 이유로 '누락'을 내면 ACK-001(고객확인 확인됨)과 모순된다.
_ACK_SUBSTITUTE_PHRASES = (
    "설명듣고이해", "설명을듣고이해", "설명을이해", "충분히설명", "설명을들었",
    "이해하였습니다", "인지하였습니다",
)


# 부정 증빙 판정은 추출 모듈의 states_unsigned 하나로 통일한다.
# 예전에는 여기에 같은 개념을 따로 구현해 뒀는데, 한쪽만 고치자 "이의 없음 확인
# 서명"에 대해 ACK-001은 위험, DOC-001은 누락을 내는 모순이 실제로 재현됐다.
_is_negative_ack = states_unsigned


def has_embedded_acknowledgement(documents: list[ParsedDocument]) -> bool:
    """설명확인 증빙이 다른 서류에 통합돼 있는지(확인 문구 + 고객 확인값)."""
    values = [v for _, v in _documents_with(documents, "customer_acknowledgement")]
    confirmed = any(v and not _is_negative_ack(v) for v in values)
    if not confirmed:
        return False
    return any(
        any(p in re.sub(r"\s+", "", d.raw_text) for p in _ACK_SUBSTITUTE_PHRASES)
        for d in documents
    )


def check_document_set(documents: list[ParsedDocument]) -> RuleCheck:
    """판매서류 4종 구비 여부 — 기록 유지·관리와 교차검증의 전제(금소법 23조)."""
    present = {d.doc_type for d in documents}
    missing = [t for t in REQUIRED_DOC_TYPES if t not in present]
    if not missing:
        return RuleCheck(
            rule_id="DOC-001",
            description="판매서류 4종 구비 여부",
            status=CheckStatus.PASS,
            document_excerpt="적합성 진단표·상품설명서·가입신청서·설명 확인서 모두 확인",
        )
    # 설명확인서만 없고 그 내용이 다른 서류에 통합돼 있으면 비대면 판매의 정상 형태다.
    # 서류 자체는 계속 요구하되(회사는 전자문서로 보유해야 한다) 위반이 아닌 확인 사항으로 낮춘다.
    if missing == ["acknowledgement"] and has_embedded_acknowledgement(documents):
        return RuleCheck(
            rule_id="DOC-001",
            description="판매서류 4종 구비 여부",
            status=CheckStatus.WARNING,
            document_excerpt="설명 확인서가 별도 파일로 없으나, 다른 서류에 설명확인 문구와 고객 확인이 포함됨",
            suggestion="비대면 판매의 전자적 확인(체크 동의·전자서명)으로 보입니다. "
            "전자문서함에 보관된 설명확인 기록을 함께 확인하세요.",
        )
    labels = ", ".join(REQUIRED_DOC_LABELS[t] for t in missing)
    return RuleCheck(
        rule_id="DOC-001",
        description="판매서류 4종 구비 여부",
        status=CheckStatus.MISSING,
        document_excerpt=f"누락 서류: {labels}",
        suggestion=f"{labels}를 업로드하세요. 서류가 빠지면 해당 항목은 검증할 수 없습니다. "
        "비대면 판매라면 전자문서함·이메일로 교부된 파일을 내려받아 올리세요.",
    )


# 녹취 의무 대상이 되는 고위험 등급(1~2등급). 실제 기준은 상품 종류(파생결합증권 등)와
# 회사 내부 기준을 함께 보지만, 서류에서 확실히 읽히는 값은 위험등급이라 이를 기준으로 삼는다.
RECORDING_RISK_GRADES = (1, 2)


def check_recording_requirement(
    documents: list[ParsedDocument],
    elderly_investor: bool = False,
    profile_min_grade: dict[str, int] | None = None,
) -> RuleCheck:
    """녹취 의무 대상 여부를 표시한다.

    이 도구는 오디오를 판독하지 않으므로 녹취가 실제로 있었는지는 **확인하지 못한다**.
    대신 '녹취가 필요한 판매 건인지'를 서류에서 판단해 담당자가 놓치지 않게 한다.
    고위험 상품·고령투자자·부적합 판매는 서류 서명만으로는 요건을 갖추지 못한다.

    elderly_investor: 만 65세 이상 여부. 우리는 개인정보(생년월일)를 추출하지 않으므로
        화면에서 검토자가 입력한다.
    profile_min_grade: 적합성 정책표. FIT-001과 **같은 표**를 써야 한다. 예전에는
        인자를 받지 않아 늘 기본표로 재계산했고, 은행이 정책을 완화하면
        FIT-001은 '적합'인데 REC-001은 '투자성향 부적합 판매'를 이유로 드는
        모순이 생겼다(실측: 안정형 고객·5등급 상품, 안정형 최소등급을 4로 완화).
    """
    risks = _documents_with(documents, "product_risk_level")
    grades = [g for _, value in risks if (g := _risk_number(value)) is not None]
    high_risk = [g for g in grades if g in RECORDING_RISK_GRADES]
    unsuitable = check_suitability(
        documents, profile_min_grade=profile_min_grade
    ).status is CheckStatus.RISK

    reasons: list[str] = []
    if high_risk:
        reasons.append(f"고위험 상품({min(high_risk)}등급)")
    if elderly_investor:
        reasons.append("고령투자자(만 65세 이상)")
    if unsuitable:
        reasons.append("투자성향 부적합 상품 판매")

    if not reasons:
        return RuleCheck(
            rule_id="REC-001",
            description="판매 과정 녹취 의무 대상 여부",
            status=CheckStatus.PASS,
            document_excerpt="녹취 의무 대상 요건에 해당하지 않습니다"
            + (f" (위험등급 {min(grades)}등급)" if grades else ""),
        )
    return RuleCheck(
        rule_id="REC-001",
        description="판매 과정 녹취 의무 대상 여부",
        status=CheckStatus.WARNING,
        document_excerpt="녹취 의무 대상일 수 있음 — " + ", ".join(reasons),
        suggestion="이 도구는 음성 파일을 판독하지 않습니다. "
        "해당 판매 건의 녹취 기록이 실제로 보관돼 있는지 별도로 확인하세요.",
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
    # 날짜가 여러 건이면 첫 값을 고르지 않고 가장 보수적으로 본다.
    # 늦은 설명일 vs 이른 계약일 → '계약 이후 설명'이 하나라도 있으면 드러난다.
    def _latest(pairs):
        dated = [(parse_iso_date(v), v) for _, v in pairs]
        dated = [(d, v) for d, v in dated if d]
        return max(dated, default=(None, pairs[0][1]))

    def _earliest(pairs):
        dated = [(parse_iso_date(v), v) for _, v in pairs]
        dated = [(d, v) for d, v in dated if d]
        return min(dated, default=(None, pairs[0][1]))

    explanation_date, explanation_value = _latest(explanation)
    contract_date, contract_value = _earliest(contract)
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
    # 확인값이 여러 건이면 첫 값이 아니라 **부정 증빙을 우선**한다.
    # 한 서류라도 미서명이면 패키지 전체가 위험이다(순서로 결과가 갈리면 안 된다).
    negatives = [v for _, v in acknowledgements if _is_negative_ack(v)]
    value = negatives[0] if negatives else acknowledgements[0][1]
    negative = bool(negatives)
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


def run_package_checks(
    documents: list[ParsedDocument],
    profile_min_grade: dict[str, int] | None = None,
    elderly_investor: bool = False,
) -> list[RuleCheck]:
    # profile_min_grade: 적합성 등급 매트릭스(규정 파라미터). 개정 시 이 값을 바꿔
    # 재검증하면 판정 변화를 확인할 수 있다(규정 개정 재검증). 기본은 현행 매트릭스.
    checks = [
        check_product_identity(documents),
        check_suitability(documents, profile_min_grade=profile_min_grade),
        check_dates(documents),
        check_acknowledgement(documents),
    ]
    checks.append(check_explanations(documents))
    checks.append(check_unfair_solicitation(documents))
    checks.append(check_document_set(documents))
    checks.append(
        check_recording_requirement(
            documents,
            elderly_investor=elderly_investor,
            profile_min_grade=profile_min_grade,
        )
    )
    return checks
