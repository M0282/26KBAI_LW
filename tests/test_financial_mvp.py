from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
from src.ingest.law_search import search_local_laws
from src.parser.financial_extractor import extract_rule_based, field_map
from src.verify.ai_reasoner import build_legal_issues
from src.verify.financial_rules import run_package_checks


def doc(document_id, doc_type, text, **fields):
    return ParsedDocument(
        document_id=document_id,
        doc_type=doc_type,
        raw_text=text,
        fields=[ParsedField(name=name, value=value) for name, value in fields.items()],
    )


def test_rule_extractor_classifies_and_normalizes():
    parsed = doc(
        "fit.pdf",
        "unknown",
        "적합성 진단표\n투자자 유형: 원금 보존 우선형\n상품명: A 펀드",
    )
    result = extract_rule_based(parsed)
    values = {field.name: field.value for field in result.fields}
    assert result.doc_type == "suitability_form"
    assert values["customer_profile"] == "안정형"


def test_suitability_mismatch_is_risk():
    documents = [
        doc("fit.pdf", "suitability_form", "투자성향: 안정형", product_name="A펀드", customer_profile="안정형"),
        doc(
            "product.pdf",
            "product_description",
            "위험등급: 1등급",
            product_name="A펀드",
            product_risk_level="1등급",
            principal_loss_explained="확인",
            risk_level_explained="확인",
            fees_explained="확인",
        ),
    ]
    checks = {check.rule_id: check for check in run_package_checks(documents)}
    assert checks["FIT-001"].status == CheckStatus.RISK


def test_product_mismatch_is_risk():
    documents = [
        doc("a.pdf", "suitability_form", "", product_code="A001"),
        doc("b.pdf", "application", "", product_code="B001"),
    ]
    checks = {check.rule_id: check for check in run_package_checks(documents)}
    assert checks["PKG-001"].status == CheckStatus.RISK


def test_explanation_missing_is_detected():
    documents = [
        doc("product.pdf", "product_description", "상품설명서", product_name="A펀드"),
    ]
    checks = {check.rule_id: check for check in run_package_checks(documents)}
    assert checks["EXP-001"].status == CheckStatus.MISSING


def test_date_after_contract_is_risk():
    documents = [
        doc("ack.pdf", "acknowledgement", "", explanation_date="2026-07-22"),
        doc("app.pdf", "application", "", contract_date="2026-07-21"),
    ]
    checks = {check.rule_id: check for check in run_package_checks(documents)}
    assert checks["DATE-001"].status == CheckStatus.RISK


def test_local_law_search_prefers_requested_article():
    chunks = [
        {"source": "금융소비자 보호에 관한 법률", "source_type": "law", "article_no": "17", "title": "적합성원칙", "text": "일반금융소비자의 투자목적과 재산상황을 파악한다."},
        {"source": "금융소비자 보호에 관한 법률", "source_type": "law", "article_no": "19", "title": "설명의무", "text": "중요한 사항을 설명한다."},
    ]
    results = search_local_laws("투자성향 적합성", chunks=chunks, preferred_articles=("17",), top_k=2)
    assert results[0].article_no == "17"


def test_branch_article_citation_uses_legal_format():
    """가지조문은 '제16조의2'다. '제16의2조'로 쓰면 조문을 잘못 인용하는 것이다."""
    chunks = [
        {
            "source": "금융소비자 보호에 관한 법률",
            "source_type": "law",
            "article_no": "21의2",
            "title": "방문판매 및 전화권유판매 관련 준수사항",
            "text": "금융상품판매업자등은 방문판매를 하는 경우 준수사항을 지켜야 한다.",
        },
        {
            "source": "금융소비자 보호에 관한 법률",
            "source_type": "law",
            "article_no": "21",
            "title": "부당권유행위 금지",
            "text": "금융상품판매업자등은 부당한 권유행위를 하여서는 아니 된다.",
        },
    ]
    # 지정 조문 경로로 조회한다. BM25는 코퍼스가 2건뿐이면 IDF가 0 이하가 되어
    # 점수 기반 결과가 비는데, 그건 검색 특성이지 인용 표기와는 무관하다.
    found = {result.article_no: result.citation for result in
             search_local_laws("방문판매 준수사항", chunks=chunks,
                               preferred_articles=("21의2", "21"), top_k=2)}
    assert found["21의2"] == "금융소비자 보호에 관한 법률 제21조의2"
    assert found["21"] == "금융소비자 보호에 관한 법률 제21조"


def test_ai_reasoner_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    documents = [doc("a.pdf", "unknown", "")]
    checks = run_package_checks(documents)
    issues = build_legal_issues(documents, checks, use_llm=True)
    assert issues["FIT-001"].search_query
    assert issues["FIT-001"].used_llm is False
