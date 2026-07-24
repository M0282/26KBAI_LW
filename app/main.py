"""KB 금융상품 판매서류 검증 AI Copilot MVP."""
from __future__ import annotations

import html
import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# .env를 읽지 않으면 키가 있어도 LLM·비전·법령 API가 전부 조용히 꺼진 채 동작한다.
# (스캔 이미지가 OCR 오독 그대로 판정되는 원인이었음) — 실제 환경변수가 우선.
load_dotenv(ROOT / ".env", override=False)

from src.common.schemas import CheckStatus, ParsedDocument
from src.ingest.law_search import find_legal_basis
from src.parser.financial_extractor import DOC_TYPES, extract_document, field_map
from src.parser.pdf_loader import load_pdf, to_parsed_document
from src.parser.pdf_render import render_highlighted_page
from src.verify.ai_reasoner import build_legal_issues
from src.verify.financial_rules import LAW_HINTS, run_package_checks

KB_YELLOW = "#FCAF17"
KB_YELLOW_ALT = "#FDB913"
KB_GRAY = "#645B4C"
STATUS_LABEL = {
    CheckStatus.PASS: ("통과", "#2E7D32"),
    CheckStatus.WARNING: ("주의", "#B26A00"),
    CheckStatus.MISSING: ("누락", "#C62828"),
    CheckStatus.RISK: ("위험", "#C62828"),
}

st.set_page_config(page_title="KB 금융상품 판매서류 검증 AI Copilot", page_icon="🛡️", layout="wide")
st.markdown(
    f"""
<style>
:root {{ --kb-yellow:{KB_YELLOW}; --kb-yellow2:{KB_YELLOW_ALT}; --kb-gray:{KB_GRAY}; }}
.stApp {{ background:linear-gradient(180deg,#fffdf7 0%,#f7f6f2 100%); }}
.block-container {{ padding-top:1.25rem; max-width:1500px; }}
.kb-hero {{ background:white; border:1px solid #eee8da; border-radius:22px; padding:24px 28px;
box-shadow:0 10px 30px rgba(100,91,76,.08); margin-bottom:18px; }}
.kb-title {{ color:{KB_GRAY}; font-size:2.1rem; font-weight:800; margin:0; }}
.kb-title b {{ color:{KB_YELLOW}; }}
.kb-sub {{ color:#655f55; margin-top:8px; font-size:1.02rem; }}
.kb-badge {{ display:inline-block; border:1px solid {KB_YELLOW}; background:#fff8df; color:{KB_GRAY};
padding:7px 12px; border-radius:999px; margin-top:13px; font-weight:700; }}
.kb-card {{ background:white; border:1px solid #eee8da; border-radius:18px; padding:18px;
box-shadow:0 8px 24px rgba(100,91,76,.07); min-height:145px; }}
.kb-step {{ border-left:5px solid {KB_YELLOW}; }}
.kb-evidence {{ background:#fff8df; border-left:4px solid {KB_YELLOW}; padding:10px 12px; border-radius:8px; }}
div[data-testid="stFileUploader"] {{ background:white; padding:12px; border-radius:16px; border:1px dashed {KB_YELLOW}; }}
.stButton button {{ background:{KB_YELLOW}; color:#332c22; border:none; font-weight:800; border-radius:10px; }}
</style>
<div class="kb-hero">
  <div class="kb-title"><b>KB</b> 금융상품 판매서류 검증 <b>AI Copilot</b></div>
  <div class="kb-sub">비정형 판매서류를 AI가 구조화하고, 문서 간 교차 검증과 현행 법령 원문 검색으로 불완전판매 위험을 사전에 확인합니다.</div>
  <div class="kb-badge">설명가능한 AI · 패키지 교차 검증 · 국가법령정보 연계</div>
</div>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("검증 설정")
    use_llm = st.toggle("LLM 문서 이해·쟁점 생성", value=bool(os.environ.get("ANTHROPIC_API_KEY")))
    live_law = st.toggle("국가법령정보 API 최신 원문 보강", value=bool(os.environ.get("LAW_API_OC")))
    st.caption("키가 없거나 호출에 실패하면 규칙 기반 추출과 로컬 법령 검색으로 자동 전환됩니다.")
    st.divider()
    st.markdown("**MVP 검증 규칙**")
    st.code("PKG-001\nFIT-001\nEXP-001\nDATE-001\nACK-001", language=None)

uploaded = st.file_uploader(
    "판매서류 패키지 업로드",
    type=["pdf", "jpg", "jpeg", "png"],
    accept_multiple_files=True,
    help="적합성 진단표, 상품설명서, 가입신청서, 설명 확인서를 함께 올리세요. "
    "PDF가 가장 정확하며, 스캔·사진·스크린샷(JPG/PNG)은 자동 OCR로 인식합니다. "
    "OCR이 흐릿한 사진·다크모드 화면을 못 읽으면 AI 비전 판독으로 자동 전환합니다.",
)

if not uploaded:
    left, center, right = st.columns(3)
    with left:
        st.markdown('<div class="kb-card"><h3>① 판매서류 패키지</h3><p>여러 PDF를 한 번에 업로드해 하나의 판매 건으로 묶습니다.</p></div>', unsafe_allow_html=True)
    with center:
        st.markdown('<div class="kb-card kb-step"><h3>② AI 문서 이해</h3><p>문서 분류, 필드 추출, 표현 정규화와 법적 검색 쟁점을 생성합니다.</p></div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="kb-card"><h3>③ 근거 기반 판정</h3><p>결정론적 규칙으로 판정하고 서류 원문과 관련 조문을 함께 보여줍니다.</p></div>', unsafe_allow_html=True)
    st.stop()

pdf_details = {}
pdf_bytes_map = {}
parsed_documents: list[ParsedDocument] = []
extraction_meta = {}
errors: list[str] = []
for file in uploaded:
    try:
        raw_bytes = file.getvalue()
        pdf = load_pdf(raw_bytes, document_id=file.name)
        parsed = to_parsed_document(pdf)
        result = extract_document(parsed, use_llm=use_llm, locator=pdf.locate)
        enriched = ParsedDocument(
            document_id=parsed.document_id,
            doc_type=result.doc_type,
            fields=result.fields,
            raw_text=parsed.raw_text,
        )
        pdf_details[file.name] = pdf
        pdf_bytes_map[file.name] = raw_bytes
        extraction_meta[file.name] = result
        parsed_documents.append(enriched)
    except Exception as exc:
        errors.append(f"{file.name}: {type(exc).__name__} - {exc}")

if errors:
    st.error("일부 문서를 읽지 못했습니다.\n\n" + "\n".join(errors))
if not parsed_documents:
    st.stop()

st.success(f"{len(parsed_documents)}개 문서를 하나의 판매 패키지로 분석했습니다.")

st.subheader("1. AI 문서 분류·핵심 필드 추출")
columns = st.columns(min(len(parsed_documents), 4))
for index, document in enumerate(parsed_documents):
    label = DOC_TYPES.get(document.doc_type, ("분류 불가", []))[0]
    meta = extraction_meta[document.document_id]
    mode = "LLM" if meta.used_llm else "규칙 폴백"
    source_pdf = pdf_details[document.document_id]
    if source_pdf.vision_applied:
        read_mode = "AI 비전 판독"  # Tesseract가 못 읽어 LLM 비전으로 전사
    elif source_pdf.ocr_applied:
        read_mode = "OCR"
    else:
        read_mode = "텍스트 레이어"
    with columns[index % len(columns)]:
        st.markdown(
            f'<div class="kb-card"><b>{html.escape(document.document_id)}</b><br>'
            f'<span style="color:{KB_YELLOW_ALT};font-weight:800">{label}</span><br>'
            f'<small>판독: {read_mode} · 추출 방식: {mode}</small></div>',
            unsafe_allow_html=True,
        )
        if meta.warning:
            st.caption(meta.warning)
        values = {name: value for name, value in field_map(document).items() if value}
        # 조건식으로 쓰면 그 결과(DeltaGenerator)가 Streamlit magic으로 화면에 덤프된다.
        if values:
            st.json(values, expanded=False)
        else:
            st.caption("추출된 핵심 필드 없음")

checks = run_package_checks(parsed_documents)
issues = build_legal_issues(parsed_documents, checks, use_llm=use_llm)

st.subheader("2. 패키지 교차 검증·법령 근거")
summary_counts = {status: sum(check.status == status for check in checks) for status in CheckStatus}
metric_cols = st.columns(4)
for col, status in zip(metric_cols, [CheckStatus.PASS, CheckStatus.WARNING, CheckStatus.MISSING, CheckStatus.RISK]):
    label, _ = STATUS_LABEL[status]
    col.metric(label, summary_counts[status])

for check in checks:
    label, color = STATUS_LABEL[check.status]
    issue = issues[check.rule_id]
    with st.expander(f"[{label}] {check.rule_id} · {check.description}", expanded=check.status != CheckStatus.PASS):
        st.markdown(f"**판정:** <span style='color:{color};font-weight:800'>{label}</span>", unsafe_allow_html=True)
        if check.document_excerpt:
            st.markdown(f'<div class="kb-evidence"><b>서류 근거</b><br>{html.escape(check.document_excerpt)}</div>', unsafe_allow_html=True)
        st.markdown(f"**AI/폴백 쟁점 설명:** {html.escape(issue.rationale)}")
        st.info(issue.recommended_action)

        hint = LAW_HINTS[check.rule_id]
        legal_results = find_legal_basis(
            issue.search_query,
            preferred_articles=hint.preferred_articles,
            preferred_sources=hint.preferred_sources,
            top_k=3,
            allow_live=live_law,
        )
        if legal_results:
            st.markdown("**관련 법령 원문 후보** (검색 상위 3건, 첫 번째가 최우선 근거)")
            for rank, result in enumerate(legal_results, start=1):
                tag = "최우선 근거" if rank == 1 else f"참고 {rank}"
                st.markdown(f"- `{tag}` **{result.citation}** · {result.title} · 출처 `{result.origin}`")
                # 조문 원문은 길어서 펼침으로 둔다(판정 화면이 법령 본문에 묻히지 않도록).
                if result.text:
                    with st.expander(f"{result.citation} 원문 보기"):
                        st.caption(result.text[:700])
        else:
            st.warning("법령 청크가 없습니다. `python -m src.ingest.fetch_regulations` 실행 또는 LAW_API_OC 설정이 필요합니다.")

st.subheader("3. 서류 원문 하이라이트")
selected_doc = st.selectbox("문서 선택", options=[document.document_id for document in parsed_documents])
selected = next(document for document in parsed_documents if document.document_id == selected_doc)
selected_pdf = pdf_details[selected_doc]
field_values = [value for value in field_map(selected).values() if value and value != "확인"]
selected_value = st.selectbox("찾을 추출값", options=field_values) if field_values else None
if selected_value:
    hits = selected_pdf.locate(selected_value)
    if hits:
        # 여러 페이지에 등장하면 전부 알려주고 골라 볼 수 있게 한다(첫 페이지만 보이던 문제).
        hit_pages = [hit["page"] for hit in hits]
        if len(hits) > 1:
            st.caption(
                f"'{selected_value}'이(가) {len(hits)}개 페이지에 등장합니다 → "
                + ", ".join(f"{page}쪽" for page in hit_pages)
            )
            chosen_page = st.selectbox("하이라이트할 페이지", options=hit_pages, key="highlight_page")
        else:
            chosen_page = hit_pages[0]
        hit = next(h for h in hits if h["page"] == chosen_page)
        image = render_highlighted_page(
            pdf_bytes_map[selected_doc],
            page_number=hit["page"],
            rects=hit["rects"],
        )
        st.image(image, caption=f"{selected_doc} · {hit['page']}페이지 · '{selected_value}' 근거 위치", use_container_width=True)
        with st.expander("좌표 데이터"):
            st.json(hits, expanded=False)
    else:
        st.warning("추출값의 정확한 좌표를 찾지 못했습니다. OCR 또는 근거 문구 정합성 보강이 필요합니다.")

st.caption("주의: 이 MVP는 법률 위반을 확정하지 않으며, 규정 준수 여부의 추가 검토가 필요한 지점을 선별합니다.")
