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


def test_integrated_acknowledgement_is_warning_when_marked_non_face_to_face():
    """비대면으로 표시된 건에서만 통합 양식을 인정한다."""
    from src.parser.financial_extractor import SIGNED

    check = check_document_set(
        _package_without_ack(SIGNED, INTEGRATED_CONTRACT), non_face_to_face=True
    )
    assert check.status is CheckStatus.WARNING
    assert "별도 파일로 없으나" in check.document_excerpt


def test_offline_sale_missing_acknowledgement_is_not_excused():
    """영업점 판매에서 설명확인서를 받지 않았다면 계약서 문구가 있어도 누락이다.

    계약서에는 확인 문구가 인쇄돼 있는 경우가 많다(실물 집합투자증권 계약서에
    '충분히 설명듣고 이해하였습니다'가 들어 있다). 채널을 모른 채 문구만 보고
    완화하면, 대면 판매의 진짜 누락이 '주의'로 가려진다.
    """
    from src.parser.financial_extractor import SIGNED

    check = check_document_set(
        _package_without_ack(SIGNED, INTEGRATED_CONTRACT), non_face_to_face=False
    )
    assert check.status is CheckStatus.MISSING
    assert "설명 확인서" in check.document_excerpt


def test_truly_missing_acknowledgement_stays_missing():
    """확인 문구도 고객 확인값도 없으면 그대로 누락이다."""
    check = check_document_set(_package_without_ack(None, "집합투자증권 저축계약서 약관"))
    assert check.status is CheckStatus.MISSING
    assert "설명 확인서" in check.document_excerpt


def test_unsigned_integrated_form_is_not_excused():
    """확인 문구가 있어도 고객이 서명하지 않았으면 면제되지 않는다."""
    from src.parser.financial_extractor import UNSIGNED

    check = check_document_set(
        _package_without_ack(UNSIGNED, INTEGRATED_CONTRACT), non_face_to_face=True
    )
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
    checks = {c.rule_id: c for c in run_package_checks(documents, non_face_to_face=True)}
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


# --- 근거 조문에서 걸리는 부분만 강조 ---

def test_focused_paragraph_picks_the_clause_that_actually_applies():
    """금소법 19조는 1,685자다. 설명 확인 의무는 ②항 한 문장이므로 그것만 보여야 한다.

    예전에는 화면이 조문 앞 700자만 잘라 보여줘서, 정작 ACK-001의 근거인
    ②항이 화면에 나오지 않았다.
    """
    from src.verify.financial_rules import LAW_HINTS, focused_law_paragraphs

    article = (
        "① 금융상품판매업자등은 일반금융소비자에게 계약 체결을 권유하는 경우에는 "
        "중요한 사항을 일반금융소비자가 이해할 수 있도록 설명하여야 한다.\n"
        "② 금융상품판매업자등은 제1항에 따른 설명에 필요한 설명서를 일반금융소비자에게 "
        "제공하여야 하며, 설명한 내용을 일반금융소비자가 이해하였음을 서명, 기명날인, "
        "녹취 또는 그 밖에 대통령령으로 정하는 방법으로 확인을 받아야 한다.\n"
        "④ 제2항에 따른 설명서의 내용은 대통령령으로 정한다."
    )
    focused = focused_law_paragraphs(article, LAW_HINTS["ACK-001"].focus)
    assert len(focused) == 1
    assert focused[0].startswith("②")
    assert "확인을 받아야 한다" in focused[0]


def test_focus_pattern_tolerates_line_breaks_inside_a_phrase():
    """조문 원문은 줄바꿈이 낱말 한가운데를 자른다 — 그래도 찾아야 한다."""
    from src.verify.financial_rules import focus_pattern

    pattern = focus_pattern(("기명날인",))
    assert pattern.search("서명, 기명\n날인, 녹취")


def test_paragraph_split_keeps_every_clause():
    from src.verify.financial_rules import split_law_paragraphs

    parts = split_law_paragraphs("① 첫째.\n② 둘째.\n③ 셋째.")
    assert [p[0] for p in parts] == ["①", "②", "③"]
    # 항 기호가 없는 조문은 통째로 한 덩이다(21조는 호로만 나뉜다).
    assert split_law_paragraphs("1. 불확실한 사항에 대하여") == ["1. 불확실한 사항에 대하여"]


def test_long_paragraph_is_narrowed_to_the_matching_items():
    """금소법 19조 ①항은 네 가지 상품의 설명 항목을 다 나열해 1,300자가 넘는다.

    이 도구는 투자성 상품만 다루므로 보험료·대출금리까지 강조하면 다시 벽이 된다.
    """
    from src.verify.financial_rules import focus_pattern, narrow_to_items

    paragraph = (
        "① 판매업자는 중요한 사항을 이해할 수 있도록 설명하여야 한다. "
        + "1. 다음 각 목의 구분에 따른 사항 "
        + "가. 보장성 상품 1) 보험료 2) 보험금 지급제한 사유 " + "가" * 200 + " "
        + "나. 투자성 상품 1) 투자성 상품의 내용 2) 투자에 따른 위험 "
        + "다. 예금성 상품 1) 이자율 " + "다" * 200
    )
    pattern = focus_pattern(("투자에 따른 위험",))
    narrowed = narrow_to_items(paragraph, pattern)
    assert "투자에 따른 위험" in narrowed
    assert "보험료" not in narrowed
    assert "이자율" not in narrowed
    # 의무를 규정한 머리 문장은 남는다.
    assert narrowed.startswith("① 판매업자는")


def test_obligation_sentence_only_when_focus_matches_the_head():
    """설명 시점처럼 의무 문장에만 걸리는 규칙은 그 문장만 보여준다."""
    from src.verify.financial_rules import focus_pattern, narrow_to_items

    paragraph = (
        "① 판매업자는 계약 체결을 권유하는 경우 설명하여야 한다. "
        + "1. 첫째 항목 " + "가" * 250 + " 2. 둘째 항목 " + "나" * 250
    )
    narrowed = narrow_to_items(paragraph, focus_pattern(("계약 체결을 권유",)))
    assert narrowed.endswith("설명하여야 한다.")
    assert "첫째 항목" not in narrowed


def test_short_paragraph_is_left_alone():
    """짧은 항은 더 쪼개지 않는다 — 문맥이 끊기는 손해가 더 크다."""
    from src.verify.financial_rules import focus_pattern, narrow_to_items

    paragraph = "② 판매업자는 1. 자료를 기록하고 2. 유지하여야 한다."
    assert narrow_to_items(paragraph, focus_pattern(("자료를 기록",))) == paragraph
