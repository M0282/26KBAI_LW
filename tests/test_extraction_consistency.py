"""추출 일관성 회귀 테스트 — 같은 양식이면 같은 결과가 나와야 한다.

실측 결함(이 테스트가 막는 것):
문장 133개가 동일한 핵심요약설명서 2건에서 한 건만 customer_acknowledgement가
나왔다. 그 문서의 '확인'은 안내 문구일 뿐 고객 확인 기록이 아니었는데도
LLM이 한 번은 True를 냈다. 값의 유무가 문서가 아니라 모델 변덕으로 갈린 것이다.

여기서는 API를 호출하지 않는 결정론적 경로만 검증한다(무료·항상 실행 가능).
"""
from src.common.schemas import ParsedDocument
from src.parser.financial_extractor import (
    DOC_TYPE_FIELDS,
    ExtractionResult,
    apply_doc_type_schema,
    extract_document,
)

# 실제 핵심요약설명서에서 가져온 안내 문구. '확인'·'서명'이 들어 있지만
# 고객이 확인한 기록이 아니라 주의사항이다.
BOILERPLATE = (
    "집합투자증권 핵심[요약] 상품설명서 (고객교부용) "
    "□ 투자자의 권리보호에 대한 안내 □ 중요내용 FAQ □ 설명의무 이행확인 "
    "고객님께서는 상품 가입 전 아래 사항을 반드시 확인·숙지하여 주시기 바랍니다. "
    "설명내용을 제대로 이해하지 못하였음에도 불구하고 설명을 이해했다는 서명을 하는 경우 "
    "원금손실이 발생할 수 있으며 예금자보호법에 따라 보호되지 않습니다. "
    "위험등급 및 보수·수수료는 (간이)투자설명서를 참조하시기 바랍니다."
)


def _parsed(document_id: str, text: str) -> ParsedDocument:
    return ParsedDocument(document_id=document_id, doc_type="unknown", fields=[], raw_text=text)


def test_same_form_yields_same_field_list():
    """상품명만 다른 동일 양식 2건 → 필드 목록이 완전히 같아야 한다."""
    a = extract_document(_parsed("a", BOILERPLATE + " 상품명: KB 내일드림 초단기채 증권 투자신탁(채권) C-E"), use_llm=False)
    b = extract_document(_parsed("b", BOILERPLATE + " 상품명: KB 삼성전자SK하이닉스 50 증권 투자신탁(채권혼합) C-E"), use_llm=False)

    assert a.doc_type == b.doc_type
    assert [f.name for f in a.fields] == [f.name for f in b.fields]


def test_doc_type_schema_is_exact():
    """유형별 고정 필드만, 순서까지 그대로 나온다(유형 밖 필드 혼입 차단)."""
    for doc_type, expected in DOC_TYPE_FIELDS.items():
        result = ExtractionResult(doc_type=doc_type, fields=[], used_llm=False)
        apply_doc_type_schema(result)
        assert tuple(f.name for f in result.fields) == expected


def test_product_description_has_no_customer_acknowledgement():
    """상품설명서에는 고객확인·계약일이 없다 — 안내 문구를 기록으로 오인하지 않는다."""
    result = ExtractionResult(doc_type="product_description", fields=[], used_llm=False)
    apply_doc_type_schema(result)
    names = {f.name for f in result.fields}
    assert "customer_acknowledgement" not in names
    assert "contract_date" not in names


def test_schema_keeps_missing_fields_as_none():
    """못 찾은 필드는 사라지지 않고 빈 값으로 남는다(미확인을 드러내기 위함)."""
    result = ExtractionResult(doc_type="product_description", fields=[], used_llm=False)
    apply_doc_type_schema(result)
    assert all(f.value is None for f in result.fields)
    assert len(result.fields) == len(DOC_TYPE_FIELDS["product_description"])


# --- 상품명 원문 대조 (PKG-001의 비교 기준값을 지키기 위한 회귀) ---
# 실측: 원문 '신한금융투자 제 23129호 파생결합증권(ELS)'을 LLM이
# '신한금융투자 23129호 파생결합증권(주가연계증권)(ELS)'로 바꿔 냈다.
# 문서에 없는 이름은 하이라이트가 불가능하고, 같은 상품을 다른 상품으로
# 판정하게 만든다.
ELS_TEXT = (
    "확인·숙지하여 주시기 바랍니다 - 신한금융투자 제 23129호 파생결합증권(ELS) "
    "(원금비보장형) 투자 위험등급 : 2등급(고위험)"
)


def test_product_name_kept_when_present_in_text():
    from src.parser.financial_extractor import ground_product_name

    name = "신한금융투자 제 23129호 파생결합증권(ELS)"
    assert ground_product_name(name, ELS_TEXT) == name


def test_product_name_ignores_whitespace_differences():
    from src.parser.financial_extractor import ground_product_name

    name = "신한금융투자제23129호파생결합증권(ELS)"
    assert ground_product_name(name, ELS_TEXT) == name


def test_hallucinated_product_name_is_repaired_to_verbatim_text():
    from src.parser.financial_extractor import ground_product_name

    repaired = ground_product_name(
        "신한금융투자 23129호 파생결합증권(주가연계증권)(ELS)", ELS_TEXT
    )
    assert repaired is not None
    # 복원값은 원문에 그대로 존재해야 한다(공백 무시 비교).
    assert repaired.replace(" ", "") in ELS_TEXT.replace(" ", "")
    assert "주가연계증권" not in repaired


def test_unrelated_product_name_is_discarded():
    from src.parser.financial_extractor import ground_product_name

    assert ground_product_name("삼성전자 우선주 ETF 상장지수펀드", ELS_TEXT) is None


# --- 고객확인은 '서명이 실제로 있는가'로 판정한다 (ACK-001) ---
# 실측: 계약서의 '서명' 언급은 전부 약관 조문이고 서명란은 텍스트상 공란인데
# LLM은 그 문구만 읽고 True를 냈다. 서명이 2쪽에 그림으로 있어 결과는 맞았지만,
# 미서명 서류였어도 똑같이 True가 나왔을 것이다.
def test_unsigned_document_is_flagged_as_risk():
    from src.common.schemas import CheckStatus, ParsedField
    from src.parser.financial_extractor import UNSIGNED
    from src.verify.financial_rules import check_acknowledgement

    docs = [ParsedDocument(
        document_id="c", doc_type="application", raw_text="x",
        fields=[ParsedField(name="customer_acknowledgement", value=UNSIGNED, confidence=1.0)],
    )]
    assert check_acknowledgement(docs).status is CheckStatus.RISK


def test_signed_document_is_not_treated_as_negative():
    from src.common.schemas import CheckStatus, ParsedField
    from src.parser.financial_extractor import SIGNED
    from src.verify.financial_rules import check_acknowledgement

    docs = [ParsedDocument(
        document_id="c", doc_type="application", raw_text="x",
        fields=[ParsedField(name="customer_acknowledgement", value=SIGNED, confidence=1.0)],
    )]
    assert check_acknowledgement(docs).status is not CheckStatus.RISK


def test_missing_acknowledgement_is_missing_not_pass():
    from src.common.schemas import CheckStatus
    from src.verify.financial_rules import check_acknowledgement

    docs = [ParsedDocument(document_id="c", doc_type="application", raw_text="x", fields=[])]
    assert check_acknowledgement(docs).status is CheckStatus.MISSING


# --- 계약일 원문 대조 (DATE-001) ---
# 실측: 계약서의 날짜는 표 양식이라 텍스트가 '년 월 일 24 07 2026'처럼
# 라벨과 값이 분리·역순으로 추출된다. 어떤 날짜 정규식으로도 파싱되지 않아
# DATE-001이 한 번도 판정되지 못했다. 비전으로 읽되 지어낸 값은 막는다.
CONTRACT_TEXT = "저축자 성명 서명(인) 생년월일 : 저축자 주소 : 년 월 일 24 07 2026 대리인 성명"


def test_scanned_date_accepted_when_digits_present_in_text():
    from src.parser.financial_extractor import ground_scanned_date

    assert ground_scanned_date("2026-07-24", CONTRACT_TEXT) == "2026-07-24"


def test_scanned_date_rejected_when_absent_from_text():
    from src.parser.financial_extractor import ground_scanned_date

    # 원문에 없는 연도 → 환각으로 보고 폐기
    assert ground_scanned_date("2019-07-24", CONTRACT_TEXT) is None


def test_scanned_date_rejects_non_date_answers():
    from src.parser.financial_extractor import ground_scanned_date

    assert ground_scanned_date("없음", CONTRACT_TEXT) is None
    assert ground_scanned_date(None, CONTRACT_TEXT) is None
    assert ground_scanned_date("2026-13-45", CONTRACT_TEXT) is None


# --- 문서유형 분류: 규칙이 확신하면 LLM보다 우선 ---
# 실측: 제목이 '상품설명 확인서'인 설명확인서를 LLM이 '상품설명서'로 오분류했다.
# doc_type은 모든 필드 게이팅의 기준이라, 오분류 하나로 고객확인·담당자 필드가
# 스키마에서 통째로 버려지고 ACK-001이 위험에서 누락으로 약해졌다.
ACK_DOC_TEXT = (
    "상품설명 확인서\n상품명: KB 글로벌 하이일드 증권투자신탁\n"
    "설명일: 2026-07-15\n설명 담당자: 이판매\n고객 확인: 미서명\n"
)


def test_confident_rule_classification_detects_acknowledgement():
    from src.parser.financial_extractor import confident_rule_doc_type

    assert confident_rule_doc_type(ACK_DOC_TEXT) == "acknowledgement"


def test_confident_classification_abstains_when_ambiguous():
    """여러 유형 키워드가 섞이면 확신하지 않는다(LLM 판단을 뒤집지 않는다)."""
    from src.parser.financial_extractor import confident_rule_doc_type

    mixed = "상품설명서 위험등급 원금손실 수수료 · 투자성향 적합성 진단 · 고객 확인 서명"
    assert confident_rule_doc_type(mixed) is None


# --- 고객확인 값 표준화 ---
# LLM이 '미서명'을 'False'로 내보내면 ACK-001의 부정어 목록에 걸리지 않아
# 미서명 서류가 통과로 판정됐다.
def test_acknowledgement_false_is_normalized_to_unsigned():
    from src.parser.financial_extractor import UNSIGNED, normalize_field

    for raw in ("False", "false", "미서명", "없음", "미확인"):
        assert normalize_field("customer_acknowledgement", raw) == UNSIGNED


def test_acknowledgement_positive_value_is_kept():
    from src.parser.financial_extractor import SIGNED, UNSIGNED, normalize_field

    assert normalize_field("customer_acknowledgement", SIGNED) != UNSIGNED


def test_signed_values_containing_없음_are_not_flagged_unsigned():
    """'특이사항 없음'처럼 정상 서명 문구에도 '없음'이 들어간다 — 오탐을 내면 안 된다."""
    from src.parser.financial_extractor import states_unsigned

    for value in ("확인함, 특이사항 없음", "서명 완료 / 누락 없음", "이의 없음 확인 서명"):
        assert states_unsigned(value) is False, value


def test_unsigned_markers_are_still_detected():
    from src.parser.financial_extractor import states_unsigned

    for value in ("미서명", "서명 없음", "미확인", "아니오", "(공란)", "없음", "미기재"):
        assert states_unsigned(value) is True, value


def test_two_digit_year_is_not_turned_into_year_26():
    """'26.07.15'를 서기 26년으로 만들면 계약 이후 설명을 정상으로 통과시킨다."""
    from src.parser.financial_extractor import _normalize_date, parse_iso_date

    assert _normalize_date("26.07.15") == "26.07.15"
    assert parse_iso_date(_normalize_date("26.07.15")) is None
    # 네 자리 연도는 그대로 정규화된다.
    assert _normalize_date("2026.07.15") == "2026-07-15"


def _product_doc(text):
    from src.common.schemas import ParsedDocument
    from src.parser.financial_extractor import extract_rule_based

    parsed = ParsedDocument(document_id="p.pdf", doc_type="product_description",
                            raw_text=text, fields=[])
    result = extract_rule_based(parsed)
    return ParsedDocument(document_id="p.pdf", doc_type="product_description",
                          raw_text=text, fields=result.fields)


def test_generic_cost_words_do_not_count_as_fee_explanation():
    """'비용'·'보수'가 엉뚱한 문맥으로만 나와도 설명 이행으로 보던 미탐."""
    from src.verify.financial_rules import _missing_explanations

    for text in (
        "상품설명서\n위험등급: 1등급\n원금손실 가능.\n본 안내장 제작 비용은 당사가 부담합니다.",
        "상품설명서\n위험등급: 1등급\n원금손실 가능.\n담당자: 김보수",
    ):
        assert "수수료·비용" in _missing_explanations(_product_doc(text)), text


def test_real_fee_wording_is_still_detected():
    """실물 설명서가 쓰는 복합어(판매수수료·운용보수)는 그대로 잡혀야 한다."""
    from src.verify.financial_rules import _missing_explanations

    text = ("상품설명서\n위험등급: 1등급\n원금손실: 원금이 보장되지 않습니다.\n"
            "수수료: 선취판매수수료 1.0%, 운용보수 연 0.7%")
    assert _missing_explanations(_product_doc(text)) == []


def test_vision_grade_rejects_answers_that_only_mention_numbers():
    """비전이 '등급을 못 찾았다'고 답한 것을 등급으로 읽으면 판정이 뒤집힌다."""
    from src.parser.financial_extractor import parse_vision_grade

    # 등급을 못 찾았다는 답 — 숫자가 섞여 있어도 채택하면 안 된다.
    assert parse_vision_grade("표시 없음(1~6 중 판단 불가)") is None
    assert parse_vision_grade("6개 항목 중 표시 없음") is None
    assert parse_vision_grade("없음") is None
    assert parse_vision_grade(None) is None
    assert parse_vision_grade(True) is None
    assert parse_vision_grade(0) is None
    assert parse_vision_grade(7) is None


def test_vision_grade_accepts_a_bare_grade():
    from src.parser.financial_extractor import parse_vision_grade

    assert parse_vision_grade("3") == "3등급"
    assert parse_vision_grade("3등급") == "3등급"
    assert parse_vision_grade(" 5 ") == "5등급"
    assert parse_vision_grade(1) == "1등급"
