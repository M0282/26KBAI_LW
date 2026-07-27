"""부당권유(ADV-001)·서류구비(DOC-001) 규칙 회귀 테스트.

실측 경고: 낱말만 보고 판정하면 오탐 100%다.
  "원금이 보장되지 않으며 전부 손실될 수 있습니다"  → 올바른 고지
  "원금보장추구형 구조화 상품"                      → 상품 유형 명칭
  "원금보장 여부와 관계없이"                        → 중립적 질의 표현
실물 코퍼스 17건에서 오탐 0건을 확인한 뒤 이 테스트로 고정한다.
"""
from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
from src.verify.financial_rules import (
    check_document_set,
    check_unfair_solicitation,
    find_guarantee_claims,
)


def _doc(document_id: str, doc_type: str, text: str = "", **fields) -> ParsedDocument:
    return ParsedDocument(
        document_id=document_id, doc_type=doc_type, raw_text=text,
        fields=[ParsedField(name=k, value=v, confidence=1.0) for k, v in fields.items()],
    )


# --- ADV-001: 부당권유 ---
def test_affirmative_guarantee_is_detected():
    for claim in ("원금이 보장되는 안전한 상품입니다", "확정수익 연 5% 지급",
                  "손실이 없는 구조입니다", "수익을 보장해 드립니다"):
        assert find_guarantee_claims(claim), claim


def test_proper_disclosure_is_not_flagged():
    """부정 고지·유형 명칭·중립 질의는 위반이 아니다."""
    for safe in ("원금이 보장되지 않으며 전부 손실될 수 있습니다",
                 "원금보장추구형 구조화 상품",
                 "원금보장 여부와 관계없이 시장상황에 따라 변동됩니다"):
        assert not find_guarantee_claims(safe), safe


def test_page_number_split_does_not_break_exception():
    """PDF 쪽번호가 낱말을 쪼개도 예외 판정이 유지된다(실측: '원금보장여-168-부')."""
    assert not find_guarantee_claims("발행조건에 따른 원금보장여 -168- 부와 관계없이")


def test_suitability_form_is_out_of_scope():
    """21조는 판매자 표현을 규율한다 — 고객이 적은 투자목적은 대상이 아니다."""
    docs = [_doc("진단표.pdf", "suitability_form", "투자목적: 원금보전 및 예금수준 안정수익")]
    assert check_unfair_solicitation(docs).status is not CheckStatus.RISK


def test_sales_document_with_guarantee_is_risk():
    docs = [_doc("설명서.pdf", "product_description", "본 상품은 원금이 보장되는 안전한 상품입니다")]
    check = check_unfair_solicitation(docs)
    assert check.status is CheckStatus.RISK
    assert "설명서.pdf" in check.document_excerpt


# --- DOC-001: 판매서류 4종 구비 ---
def test_complete_document_set_passes():
    docs = [_doc("a", "suitability_form"), _doc("b", "product_description"),
            _doc("c", "application"), _doc("d", "acknowledgement")]
    assert check_document_set(docs).status is CheckStatus.PASS


def test_missing_document_is_named():
    docs = [_doc("a", "suitability_form"), _doc("b", "product_description"),
            _doc("c", "application")]
    check = check_document_set(docs)
    assert check.status is CheckStatus.MISSING
    assert "설명 확인서" in check.document_excerpt
