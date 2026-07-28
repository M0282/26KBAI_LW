"""KB 서식 인식·전용 규칙 회귀 테스트.

KB 서식은 고정 양식이라 고유 문구만으로 구분된다(실물 9건 100% 정확).
분류에 LLM을 쓰지 않으므로 이 테스트가 곧 분류 정확도 보증이다.
"""
from __future__ import annotations

from src.common.schemas import CheckStatus, ParsedDocument, ParsedField
from src.parser.kb_documents import FORM_BY_CODE, classify_kb_form, missing_forms
from src.verify.kb_rules import check_grade_agreement, check_kb_document_set

# 실물 서류에서 발췌한 시그니처 문구
SAMPLES = {
    "kb_suitability": "투자성향분석 이수빈님의 투자성향은 위험중립형이며 4등급(보통위험) 이하",
    "kb_simple_prospectus": "간이투자설명서 KB 내일드림 초단기채 [펀드코드: E2802] 투자위험등급 "
                            "증권신고서 효력발생일",
    "kb_key_summary": "집합투자증권 핵심[요약] 상품설명서 (고객교부용) 당행부여 위험등급",
    "kb_contract": "집합투자증권 저축계약서 저축자 성명 집합투자상품통장을 교부한다",
}


def _doc(doc_type: str, **fields) -> ParsedDocument:
    return ParsedDocument(
        document_id="x", doc_type=doc_type, raw_text="x",
        fields=[ParsedField(name=k, value=v, confidence=1.0) for k, v in fields.items()],
    )


def test_each_kb_form_is_recognized():
    for expected, text in SAMPLES.items():
        assert classify_kb_form(text) == expected, expected


def test_unrelated_text_is_not_forced_into_a_form():
    """어느 시그니처도 없으면 추측하지 않는다."""
    assert classify_kb_form("오늘 점심은 김치찌개입니다") is None


def test_missing_forms_are_listed():
    present = {"kb_suitability", "kb_simple_prospectus"}
    missing = {f.code for f in missing_forms(present)}
    assert missing == {"kb_key_summary", "kb_contract"}


def test_complete_kb_set_passes():
    assert check_kb_document_set(set(FORM_BY_CODE)).status is CheckStatus.PASS


# --- KB-GRADE: 운용사 등급 ↔ 당행부여 등급 (감독규정 12조) ---
def test_matching_grades_pass():
    forms = {
        "kb_simple_prospectus": _doc("product_description", product_risk_level="4등급"),
        "kb_key_summary": _doc("product_description", product_risk_level="4등급"),
    }
    assert check_grade_agreement(forms).status is CheckStatus.PASS


def test_differing_grades_are_risk():
    """판매사 등급이 발행인과 다르면 협의 여부를 확인해야 한다."""
    forms = {
        "kb_simple_prospectus": _doc("product_description", product_risk_level="2등급"),
        "kb_key_summary": _doc("product_description", product_risk_level="4등급"),
    }
    check = check_grade_agreement(forms)
    assert check.status is CheckStatus.RISK
    assert "상이" in check.document_excerpt


def test_grade_check_needs_both_documents():
    forms = {"kb_simple_prospectus": _doc("product_description", product_risk_level="4등급")}
    assert check_grade_agreement(forms).status is CheckStatus.MISSING
