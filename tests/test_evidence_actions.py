"""구조화 원문 근거·공식 조치 계획 회귀 테스트."""
from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
from src.verify.ai_reasoner import build_legal_issues
from src.verify.financial_rules import run_package_checks


def _doc(document_id: str, doc_type: str, raw_text: str = "", **fields) -> ParsedDocument:
    return ParsedDocument(
        document_id=document_id,
        doc_type=doc_type,
        raw_text=raw_text,
        fields=[
            ParsedField(
                name=name,
                value=value,
                page=1,
                confidence=0.9,
                evidence_text=f"{name}: {value}" if value else None,
            )
            for name, value in fields.items()
        ],
    )


def _complete_package(profile: str = "안정형", grade: str = "6등급") -> list[ParsedDocument]:
    return [
        _doc(
            "01_적합성진단표.pdf", "suitability_form", "투자성향: 안정형",
            customer_profile=profile,
        ),
        _doc(
            "02_상품설명서.pdf", "product_description",
            "상품명: KB 테스트펀드 위험등급: 6등급 원금손실 수수료",
            product_name="KB 테스트펀드", product_risk_level=grade,
            principal_loss_explained="확인", risk_level_explained="확인",
            fees_explained="확인",
        ),
        _doc(
            "03_가입신청서.pdf", "application",
            "상품명: KB 테스트펀드 계약일: 2026-07-20",
            product_name="KB 테스트펀드", contract_date="2026-07-20",
        ),
        _doc(
            "04_설명확인서.pdf", "acknowledgement",
            "상품명: KB 테스트펀드 설명일: 2026-07-19 고객확인: 서명완료 담당자: 김판매",
            product_name="KB 테스트펀드", explanation_date="2026-07-19",
            customer_acknowledgement="서명완료", staff_name="김판매",
        ),
    ]


def test_fit_risk_links_both_documents_and_has_blocking_action():
    checks = {c.rule_id: c for c in run_package_checks(_complete_package("안정형", "3등급"))}
    fit = checks["FIT-001"]

    assert fit.status is CheckStatus.RISK
    assert {item.field_name for item in fit.evidence_items} == {
        "customer_profile", "product_risk_level"
    }
    assert {item.document_id for item in fit.evidence_items} == {
        "01_적합성진단표.pdf", "02_상품설명서.pdf"
    }
    assert fit.action_plan is not None
    assert fit.action_plan.responsible_role == "판매 담당자"
    assert fit.action_plan.sale_blocking is True
    assert "재검증" in fit.action_plan.completion_criteria


def test_missing_explanation_records_which_field_was_absent():
    documents = _complete_package()
    product = next(d for d in documents if d.doc_type == "product_description")
    fee = next(f for f in product.fields if f.name == "fees_explained")
    fee.value = None
    fee.evidence_text = None

    exp = {c.rule_id: c for c in run_package_checks(documents)}["EXP-001"]
    assert exp.status is CheckStatus.MISSING
    missing = [item for item in exp.evidence_items if item.evidence_type == "missing_field"]
    assert any(item.document_id == "02_상품설명서.pdf" for item in missing)
    assert any(item.field_name == "fees_explained" for item in missing)
    assert exp.action_plan and exp.action_plan.sale_blocking is True


def test_missing_document_is_structured_and_assigns_owner():
    documents = [d for d in _complete_package() if d.doc_type != "acknowledgement"]
    doc_check = {c.rule_id: c for c in run_package_checks(documents)}["DOC-001"]

    assert doc_check.status is CheckStatus.MISSING
    assert any(
        item.evidence_type == "missing_document"
        and item.field_name == "acknowledgement"
        for item in doc_check.evidence_items
    )
    assert doc_check.action_plan is not None
    assert doc_check.action_plan.responsible_role == "판매 담당자"
    assert doc_check.action_plan.sale_blocking is True


def test_pass_has_no_unnecessary_action():
    checks = run_package_checks(_complete_package())
    fit = next(c for c in checks if c.rule_id == "FIT-001")
    assert fit.status is CheckStatus.PASS
    assert fit.action_plan is None


def test_llm_reasoner_cannot_replace_official_action():
    checks = run_package_checks(_complete_package("안정형", "3등급"))
    fit = next(c for c in checks if c.rule_id == "FIT-001")
    issue = build_legal_issues(_complete_package("안정형", "3등급"), checks, use_llm=False)["FIT-001"]

    assert fit.action_plan is not None
    assert issue.recommended_action == fit.action_plan.required_action
