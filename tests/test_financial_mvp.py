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


def test_ai_reasoner_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    documents = [doc("a.pdf", "unknown", "")]
    checks = run_package_checks(documents)
    issues = build_legal_issues(documents, checks, use_llm=True)
    assert issues["FIT-001"].search_query
    assert issues["FIT-001"].used_llm is False


def test_drop_heading_echo_removes_repeated_article_title():
    """API가 조문 머리말을 본문에 한 번 더 넣어 준다 — 원문 보기에 군더더기로 남는다."""
    from src.ingest.articles import drop_heading_echo

    fragments = [
        "① 금융상품직접판매업자는 계약서류를 지체 없이 제공하여야 한다.",
        "② 다툼이 있는 경우에는 판매업자가 이를 증명하여야 한다.",
        "제23조(계약서류의 제공의무)",
    ]
    assert drop_heading_echo(fragments) == fragments[:2]


def test_drop_heading_echo_keeps_article_that_is_only_a_heading():
    """본문이 머리말뿐인 조문은 지울 것이 아니라 그게 전부다."""
    from src.ingest.articles import drop_heading_echo

    assert drop_heading_echo(["제6조(삭제)"]) == ["제6조(삭제)"]


def test_repealed_articles_are_never_offered_as_legal_basis():
    """폐지된 조문을 근거로 내놓으면 그 판정은 통째로 틀린다."""
    from src.ingest.law_search import is_repealed

    chunks = [
        {"source": "은행업감독규정", "source_type": "admrule", "article_no": "16",
         "title": "", "text": "제16조 삭제 <2016.7.28>"},
        {"source": "은행업감독규정", "source_type": "admrule", "article_no": "17",
         "title": "리스크관리", "text": "제17조(리스크관리조직) 삭제 <2014.11.1>"},
        {"source": "은행업감독규정", "source_type": "admrule", "article_no": "18",
         "title": "경영지도기준", "text": "은행은 경영지도기준을 준수하여야 한다. 삭제된 조항을 참고한다."},
    ]
    assert is_repealed(chunks[0]) is True
    assert is_repealed(chunks[1]) is True
    # 본문에 '삭제'라는 낱말이 들어 있을 뿐인 살아 있는 조문은 남아야 한다.
    assert is_repealed(chunks[2]) is False

    results = search_local_laws("삭제된 조항 경영지도기준", chunks=chunks, top_k=5)
    assert all(not r.text.strip().endswith("삭제") for r in results)
    assert {r.article_no for r in results} <= {"18"}
