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


def test_top_basis_prefers_the_article_that_actually_contains_the_clause():
    """조문 번호가 같아도 법률과 감독규정은 내용이 전혀 다르다.

    실측: DOC-001의 최우선 근거로 금소법 제23조(계약서류의 제공의무) 대신
    감독규정 제23조(중개업자의 고지의무)가 떴다. 화면에는 '최우선 근거'라면서
    '연결되는 문구를 찾지 못했다'가 함께 뜨는 자기모순이었다.
    """
    from src.ingest.law_search import search_chunks, _rank_basis

    chunks = [
        {"source": "금융소비자 보호에 관한 감독규정", "source_type": "admrule",
         "article_no": "23", "title": "금융상품판매대리ㆍ중개업자의 고지의무",
         "text": "영 제24조제1항제4호에서 금융위원회가 정하여 고시하는 사항이란 "
                 "중개업자의 고지의무에 관한 사항을 말한다."},
        {"source": "금융소비자 보호에 관한 법률", "source_type": "law",
         "article_no": "23", "title": "계약서류의 제공의무",
         "text": "① 금융상품직접판매업자는 계약을 체결하는 경우 "
                 "계약서류를 금융소비자에게 지체 없이 제공하여야 한다."},
    ]
    focus = ("계약서류를 금융소비자에게 지체 없이 제공", "증명하여야 한다")
    found = search_chunks("중개업자 고지의무", chunks,
                          preferred_articles=("23",), top_k=2)
    ranked = _rank_basis(found, ("23",), focus)
    assert ranked[0].source == "금융소비자 보호에 관한 법률"
    assert ranked[0].title == "계약서류의 제공의무"


def test_ranking_without_focus_keeps_score_order():
    """focus 를 주지 않으면 종전처럼 지정 조문·점수 순서를 유지한다."""
    from src.ingest.law_search import LawSearchResult, _rank_basis

    a = LawSearchResult("법", "law", "23", "가", "본문 가", 9.0)
    b = LawSearchResult("규정", "admrule", "23", "나", "본문 나", 3.0)
    assert [r.title for r in _rank_basis([a, b], ("23",), ())] == ["가", "나"]


def test_related_articles_only_no_forced_count():
    """개수를 채우려고 관련 없는 조문을 끼워 넣지 않는다."""
    from src.ingest.law_search import LawSearchResult, _keep_related

    hit = LawSearchResult("법", "law", "23", "계약서류의 제공의무",
                          "계약서류를 금융소비자에게 지체 없이 제공하여야 한다.", 9.0)
    miss = LawSearchResult("규정", "admrule", "23", "중개업자의 고지의무",
                           "중개업자는 고지의무를 이행해야 한다.", 8.0)
    focus = ("계약서류를 금융소비자에게 지체 없이 제공",)
    assert _keep_related([hit, miss], focus, 3) == [hit]


def test_keeps_one_article_when_nothing_matches():
    """연결되는 조문이 없어도 확인의 출발점 1건은 남긴다."""
    from src.ingest.law_search import LawSearchResult, _keep_related

    a = LawSearchResult("법", "law", "1", "가", "관련 없는 본문", 9.0)
    b = LawSearchResult("법", "law", "2", "나", "역시 관련 없음", 8.0)
    assert _keep_related([a, b], ("있을 수 없는 문구",), 3) == [a]


def test_no_focus_keeps_previous_behaviour():
    from src.ingest.law_search import LawSearchResult, _keep_related

    a = LawSearchResult("법", "law", "1", "가", "본문", 9.0)
    b = LawSearchResult("법", "law", "2", "나", "본문", 8.0)
    assert _keep_related([a, b], (), 2) == [a, b]


def test_sanction_articles_are_not_offered_as_basis():
    """과태료·벌칙은 위반의 결과다. 의무의 근거로 내밀면 안 된다."""
    from src.ingest.law_search import is_sanction

    assert is_sanction({"title": "과태료"}) is True
    assert is_sanction({"title": "벌칙"}) is True
    assert is_sanction({"title": "과징금의 부과"}) is True
    assert is_sanction({"title": "금융상품판매업자등에 대한 처분 등"}) is True
    assert is_sanction({"title": "설명의무"}) is False
    assert is_sanction({"title": "계약서류의 제공의무"}) is False


def test_primary_source_is_limited_to_declared_articles():
    """법률 근거는 규칙마다 확정해 두었다. 흔한 표현으로 다른 조문이 끼면 안 된다."""
    from src.ingest.law_search import LawSearchResult, _keep_related

    declared = LawSearchResult("금융소비자 보호에 관한 법률", "law", "19", "설명의무",
                               "설명한 내용을 이해하였음을 확인을 받아야 한다.", 9.0)
    other = LawSearchResult("금융소비자 보호에 관한 법률", "law", "17", "적합성원칙",
                            "정보를 파악하고 이해하였음을 확인을 받아야 한다.", 9.5)
    kept = _keep_related(
        [other, declared], ("이해하였음을",), 3,
        preferred_sources=("금융소비자 보호에 관한 법률",), preferred_articles=("19",),
    )
    assert [r.article_no for r in kept] == ["19"]


def test_short_single_word_match_is_not_grounding():
    """'녹취' 두 글자는 여러 조문에 나온다 — 그 한 마디로 근거를 삼지 않는다."""
    from src.ingest.law_search import LawSearchResult, _keep_related

    weak = LawSearchResult("금융소비자 보호에 관한 감독규정", "admrule", "14",
                           "불공정영업행위의 금지", "녹취 방법을 준용한다.", 9.0)
    strong = LawSearchResult("금융소비자 보호에 관한 감독규정", "admrule", "12",
                             "설명의무", "위험등급을 정하는 경우에 지켜야 한다.", 8.0)
    kept = _keep_related([weak, strong], ("녹취", "위험등급을 정하는 경우"), 3)
    assert [r.article_no for r in kept] == ["12"]


def test_every_law_hint_phrase_exists_in_the_corpus():
    """문구가 코퍼스에 없으면 근거가 조용히 비어 버린다.

    화면에는 오류가 아니라 '연결되는 문구를 찾지 못했다'로 뜨기 때문에
    오타나 조문 개정을 알아채기 어렵다. 실측 — 법령이 낱말 사이에 괄호
    정의문을 끼워 넣어서('위험등급(이하 "위험등급"이라 한다)에 관한 정보와
    비교하여 평가할 것') 눈으로 읽고 적은 문구 2건이 죽어 있었다.
    """
    from src.ingest.law_search import is_repealed, load_article_chunks
    from src.verify.financial_rules import LAW_HINTS, focus_pattern

    scope = ("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정")
    chunks = [
        c for c in load_article_chunks()
        if not is_repealed(c) and c.get("source") in scope
    ]
    if not chunks:  # 코퍼스가 없는 환경에서는 검사할 것이 없다
        return

    dead = []
    for rule, hint in LAW_HINTS.items():
        for phrase in (*hint.focus, *hint.anchor):
            pattern = focus_pattern((phrase,))
            if not any(pattern.search(c.get("text") or "") for c in chunks):
                dead.append(f"{rule}: {phrase!r}")
    assert not dead, "코퍼스에 없는 문구: " + ", ".join(dead)


def test_declared_basis_does_not_depend_on_the_search_query():
    """근거 조문은 검색어와 무관해야 한다.

    앱은 LLM이 만든 검색어를 쓴다. 검색으로 근거를 고르면 같은 서류·같은
    판정인데 근거가 흔들린다(실측: 8종 중 5종. '확정수익 표현 검사' 질의에서
    부당권유 판정의 최우선 근거가 제21조 대신 제19조가 됐다).
    """
    from src.ingest.law_search import load_article_chunks
    from src.verify.financial_rules import LAW_HINTS

    if not load_article_chunks():  # 코퍼스가 없는 환경
        return

    from src.ingest.law_search import find_legal_basis

    for rule, hint in LAW_HINTS.items():
        seen = set()
        for query in (hint.query, "전혀 다른 질의 광고 시 금지행위", "계약서류 전자우편"):
            results = find_legal_basis(
                query, preferred_articles=hint.preferred_articles,
                preferred_sources=hint.preferred_sources, top_k=3,
                allow_live=False, focus=hint.grounding, basis=hint.basis,
            )
            seen.add(tuple(r.citation for r in results))
        assert len(seen) == 1, f"{rule}: 검색어에 따라 근거가 달라진다 {seen}"


def test_every_declared_basis_article_exists_and_is_highlightable():
    """선언한 근거 조문이 코퍼스에 있고, 강조할 문구가 그 안에 있어야 한다."""
    from src.ingest.law_search import fetch_declared_basis, load_article_chunks
    from src.verify.financial_rules import LAW_HINTS, focused_law_paragraphs

    if not load_article_chunks():
        return

    for rule, hint in LAW_HINTS.items():
        results = fetch_declared_basis(hint.basis, allow_live=False)
        assert len(results) == len(hint.basis), f"{rule}: 선언한 조문을 찾지 못했다"
        for result in results:
            assert focused_law_paragraphs(result.text, hint.focus), (
                f"{rule}: {result.citation} 에 강조할 문구가 없다"
            )
