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


def test_ack_and_doc_rules_agree_on_the_same_value():
    """부정 증빙 판정이 두 곳에 따로 있으면 같은 서류에 모순된 판정이 나온다.

    실측: '이의 없음 확인 서명'에 대해 ACK-001은 위험, DOC-001은 누락이 나왔다.
    (비대면 통합 양식 — 설명확인서가 가입신청서에 포함된 정상 형태)
    """
    from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
    from src.verify.financial_rules import run_package_checks

    def build(doc_id, doc_type, text, **fields):
        return ParsedDocument(
            document_id=doc_id, doc_type=doc_type, raw_text=text,
            fields=[ParsedField(name=k, value=v) for k, v in fields.items()],
        )

    documents = [
        build("01.pdf", "suitability_form", "투자성향: 안정형",
              customer_profile="안정형", product_name="KB 펀드"),
        build("02.pdf", "product_description", "위험등급: 6등급",
              product_name="KB 펀드", product_risk_level="6등급",
              principal_loss_explained="확인", risk_level_explained="확인",
              fees_explained="확인"),
        build("03.pdf", "application",
              "위 계약내용에 대해 모두 확인하였으며 주요내용을 충분히 설명듣고 이해하였습니다.",
              product_name="KB 펀드", contract_date="2026-07-20",
              customer_acknowledgement="이의 없음 확인 서명", staff_name="이판매"),
    ]
    checks = {c.rule_id: c for c in run_package_checks(documents)}
    assert checks["ACK-001"].status == CheckStatus.PASS
    assert checks["DOC-001"].status == CheckStatus.WARNING


def test_recording_rule_uses_the_same_policy_table_as_suitability():
    """REC-001이 정책표를 못 받아 기본표로 재계산하면 FIT-001과 어긋난다."""
    from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
    from src.verify.financial_rules import (
        DEFAULT_PROFILE_MIN_ALLOWED_GRADE,
        run_package_checks,
    )

    def build(doc_id, doc_type, **fields):
        return ParsedDocument(
            document_id=doc_id, doc_type=doc_type, raw_text="",
            fields=[ParsedField(name=k, value=v) for k, v in fields.items()],
        )

    documents = [
        build("f.pdf", "suitability_form", customer_profile="안정형"),
        build("p.pdf", "product_description", product_risk_level="5등급"),
    ]
    # 은행이 정책을 완화한 경우: 안정형도 4등급까지 가입 가능
    relaxed = dict(DEFAULT_PROFILE_MIN_ALLOWED_GRADE, **{"안정형": 4})
    checks = {c.rule_id: c for c in run_package_checks(documents, profile_min_grade=relaxed)}

    assert checks["FIT-001"].status == CheckStatus.PASS
    # FIT이 적합하다고 한 건을 REC가 '부적합 판매'라고 부르면 안 된다.
    assert "부적합" not in checks["REC-001"].document_excerpt

    # 기본표에서는 부적합이 맞으므로 그 이유가 유지돼야 한다.
    default_checks = {c.rule_id: c for c in run_package_checks(documents)}
    assert default_checks["FIT-001"].status == CheckStatus.RISK
    assert "부적합" in default_checks["REC-001"].document_excerpt


def test_yeobu_survives_margin_label_inserted_between_syllables():
    """실물 서류는 여백 라벨이 낱말 한가운데를 가른다 — 신한 ELS 핵심설명서 실측.

        …발행조건에 따른 원금보장여
        - 168 -
        투자자
        유의사항
        부와 관계없이 시장상황에 따라 원금손실이 발생할 수 있습니다.

    쪽번호만 지워서는 '여부'가 붙지 않는다. 정상 위험고지를 부당권유로
    잡으면 안 된다.
    """
    from src.verify.financial_rules import find_guarantee_claims

    text = (
        "본 증권을 만기 이전에 중도환매 할 경우 발행조건에 따른 원금보장여\n"
        "- 168 -\n\n투자자\n유의사항\n"
        "부와 관계없이 시장상황에 따라 원금손실이 발생할 수 있습니다.\n"
    )
    assert find_guarantee_claims(text) == []
    # 그렇다고 '하여'까지 삼키면 안 된다.
    assert find_guarantee_claims("수익을 보장하여 드립니다.")
