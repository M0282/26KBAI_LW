"""부당권유(ADV-001)·서류구비(DOC-001) 규칙 회귀 테스트.

실측 경고: 낱말만 보고 판정하면 오탐 100%다.
  "원금이 보장되지 않으며 전부 손실될 수 있습니다"  → 올바른 고지
  "원금보장추구형 구조화 상품"                      → 상품 유형 명칭
  "원금보장 여부와 관계없이"                        → 중립적 질의 표현
실물 코퍼스 17건에서 오탐 0건을 확인한 뒤 이 테스트로 고정한다.
"""
from __future__ import annotations
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


# --- 비대면 판매: 설명확인서가 다른 서류에 통합된 경우 ---
# 모바일 펀드 가입에서는 설명확인서가 별도 파일로 존재하지 않고, 가입신청서 안의
# 확인 문구 + 전자서명으로 대체된다. 실측 — 집합투자증권 계약서에 다음이 있다:
#   "위 계약내용에 대해 모두 확인하였으며, 주요내용을 충분히 설명듣고 이해하였습니다"
# 이때 '누락'을 내면 ACK-001(고객확인 확인됨)과 모순된다. 서류는 계속 요구하되
# 위반이 아닌 확인 사항으로 낮춘다.
INTEGRATED_CONTRACT = (
    "집합투자증권 저축계약서\n위 계약내용에 대해 모두 확인하였으며, "
    "주요내용을 충분히 설명듣고 이해하였습니다.\n저축자 성명 서명(인)"
)


def _package_without_ack(ack_value: str | None, contract_text: str):
    docs = [
        _doc("진단표", "suitability_form", customer_profile="위험중립형"),
        _doc("설명서", "product_description", product_risk_level="4등급"),
        _doc("계약서", "application", contract_text,
             **({"customer_acknowledgement": ack_value} if ack_value else {})),
    ]
    return docs


def test_integrated_acknowledgement_is_warning_not_missing():
    from src.parser.financial_extractor import SIGNED

    check = check_document_set(_package_without_ack(SIGNED, INTEGRATED_CONTRACT))
    assert check.status is CheckStatus.WARNING
    assert "별도 파일로 없으나" in check.document_excerpt


def test_truly_missing_acknowledgement_stays_missing():
    """확인 문구도 고객 확인값도 없으면 그대로 누락이다."""
    check = check_document_set(_package_without_ack(None, "집합투자증권 저축계약서 약관"))
    assert check.status is CheckStatus.MISSING
    assert "설명 확인서" in check.document_excerpt


def test_unsigned_integrated_form_is_not_excused():
    """확인 문구가 있어도 고객이 서명하지 않았으면 면제되지 않는다."""
    from src.parser.financial_extractor import UNSIGNED

    check = check_document_set(_package_without_ack(UNSIGNED, INTEGRATED_CONTRACT))
    assert check.status is CheckStatus.MISSING


# --- REC-001: 녹취 의무 대상 표시 ---
# 이 도구는 오디오를 판독하지 않는다. 녹취가 있었는지는 확인하지 못하므로
# '녹취가 필요한 판매 건인지'만 표시해 담당자가 놓치지 않게 한다.
def _rec_docs(profile: str, grade: str):
    return [
        _doc("진단표", "suitability_form", customer_profile=profile),
        _doc("설명서", "product_description", product_risk_level=grade),
    ]


def test_ordinary_sale_is_not_recording_target():
    from src.verify.financial_rules import check_recording_requirement

    check = check_recording_requirement(_rec_docs("적극투자형", "4등급"))
    assert check.status is CheckStatus.PASS


def test_high_risk_product_flags_recording():
    from src.verify.financial_rules import check_recording_requirement

    check = check_recording_requirement(_rec_docs("공격투자형", "1등급"))
    assert check.status is CheckStatus.WARNING
    assert "고위험" in check.document_excerpt


def test_elderly_investor_flags_recording_even_on_low_risk():
    from src.verify.financial_rules import check_recording_requirement

    check = check_recording_requirement(_rec_docs("적극투자형", "4등급"), elderly_investor=True)
    assert check.status is CheckStatus.WARNING
    assert "고령투자자" in check.document_excerpt


def test_unsuitable_sale_flags_recording():
    """부적합 상품을 판매하면 서류 서명만으로 요건을 갖추지 못한다."""
    from src.verify.financial_rules import check_recording_requirement

    check = check_recording_requirement(_rec_docs("안정형", "3등급"))
    assert check.status is CheckStatus.WARNING
    assert "부적합" in check.document_excerpt


def test_recording_check_never_claims_to_verify_audio():
    """음성을 판독하지 않는다는 사실을 반드시 밝힌다(과장 방지)."""
    from src.verify.financial_rules import check_recording_requirement

    check = check_recording_requirement(_rec_docs("공격투자형", "1등급"))
    assert "음성 파일을 판독하지 않습니다" in (check.suggestion or "")


def test_guarantee_claim_survives_connective_hayeo():
    """'하여·위하여'가 '여' 예외에 걸려 명백한 위반을 놓치던 문제."""
    from src.verify.financial_rules import find_guarantee_claims

    assert find_guarantee_claims("수익을 보장하여 드립니다.")


def test_guarantee_claim_ignores_yeobu_question():
    """'원금보장 여부'는 중립적 질의지 보장 약속이 아니다."""
    from src.verify.financial_rules import find_guarantee_claims

    assert not find_guarantee_claims("원금보장 여부를 확인하시기 바랍니다.")
    # 쪽번호가 낱말 사이에 끼어도 '여부'는 복원된다.
    assert not find_guarantee_claims("원금보장여 - 168 - 부와 관계없이 손실이 발생할 수 있습니다.")


def test_guarantee_claim_still_ignores_proper_disclosure():
    from src.verify.financial_rules import find_guarantee_claims

    assert not find_guarantee_claims("본 상품은 원금이 보장되지 않으며 전부 손실될 수 있습니다.")
    assert not find_guarantee_claims("원금보장추구형 구조화 상품")
