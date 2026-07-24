"""판정은 업로드 순서에 흔들리면 안 된다.

실측 결함: 규칙들이 패키지에서 첫 값만 골랐다(profiles[0], risks[0],
explanation[0], acknowledgements[0], product_documents[0]). 그래서 같은 서류
묶음이라도 파일을 올린 순서에 따라 통과/위험이 갈렸다.

판단은 늘 가장 보수적인 값으로 한다.
- FIT-001 : 가장 위험한 등급(숫자가 작은 쪽), 감내도가 가장 낮은 성향
- DATE-001: 늦은 설명일 vs 이른 계약일
- ACK-001 : 한 서류라도 미서명이면 위험
- EXP-001 : 모든 상품설명서를 검사
"""
import itertools

from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
from src.verify.financial_rules import run_package_checks


def _doc(document_id: str, doc_type: str, **fields) -> ParsedDocument:
    return ParsedDocument(
        document_id=document_id, doc_type=doc_type, raw_text="x",
        fields=[ParsedField(name=k, value=v, confidence=1.0) for k, v in fields.items()],
    )


def _package() -> list[ParsedDocument]:
    return [
        _doc("진단표.jpg", "suitability_form", customer_profile="위험중립형",
             explanation_date="2026-07-25", customer_acknowledgement="미서명"),
        _doc("설명서A.pdf", "product_description", product_name="펀드",
             product_risk_level="5등급", principal_loss_explained="확인",
             risk_level_explained="확인", fees_explained="확인"),
        _doc("설명서B.pdf", "product_description", product_name="펀드",
             product_risk_level="2등급", principal_loss_explained="확인",
             risk_level_explained="확인", fees_explained="확인"),
        _doc("계약서.pdf", "application", product_name="펀드",
             contract_date="2026-07-24", customer_acknowledgement="확인(서명 기재)"),
    ]


def _verdicts(documents):
    return tuple((c.rule_id, c.status) for c in run_package_checks(list(documents)))


def test_verdicts_are_identical_for_every_upload_order():
    results = {_verdicts(order) for order in itertools.permutations(_package())}
    assert len(results) == 1, f"업로드 순서에 따라 판정이 갈린다: {results}"


def test_conservative_choice_uses_riskiest_grade():
    """5등급과 2등급이 섞이면 위험중립형(허용 4등급 이상) 기준으로 위험이어야 한다."""
    verdicts = dict(_verdicts(_package()))
    assert verdicts["FIT-001"] is CheckStatus.RISK


def test_any_unsigned_document_makes_acknowledgement_risky():
    verdicts = dict(_verdicts(_package()))
    assert verdicts["ACK-001"] is CheckStatus.RISK


def test_explanation_after_contract_is_detected():
    """설명일 2026-07-25 > 계약일 2026-07-24 → 계약 이후 설명."""
    verdicts = dict(_verdicts(_package()))
    assert verdicts["DATE-001"] is CheckStatus.RISK


def test_all_product_documents_are_checked():
    """두 번째 상품설명서에만 설명이 빠져도 드러나야 한다."""
    docs = [
        _doc("A.pdf", "product_description", product_name="펀드",
             principal_loss_explained="확인", risk_level_explained="확인",
             fees_explained="확인"),
        _doc("B.pdf", "product_description", product_name="펀드",
             principal_loss_explained="확인", risk_level_explained="확인"),
    ]
    for order in itertools.permutations(docs):
        check = next(c for c in run_package_checks(list(order)) if c.rule_id == "EXP-001")
        assert check.status is CheckStatus.MISSING
        assert "B.pdf" in check.document_excerpt
