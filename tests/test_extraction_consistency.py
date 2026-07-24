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
