"""KB 금융상품 판매서류 검증 AI Copilot MVP."""
from __future__ import annotations

import html
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# .env를 읽지 않으면 키가 있어도 LLM·비전·법령 API가 전부 조용히 꺼진 채 동작한다.
# (스캔 이미지가 OCR 오독 그대로 판정되는 원인이었음) — 실제 환경변수가 우선.
load_dotenv(ROOT / ".env", override=False)

from src.common.llm_cache import clear_llm_cache
from src.common.schemas import CheckStatus, ParsedDocument
from src.ingest.law_search import find_legal_basis
from src.parser.financial_extractor import DOC_TYPES, extract_document, field_map
from src.parser.pdf_loader import load_pdf, to_parsed_document
from src.parser.pdf_render import render_highlighted_page
from src.verify.ai_reasoner import build_legal_issues
from src.verify.financial_rules import (
    DEFAULT_PROFILE_MIN_ALLOWED_GRADE,
    LAW_HINTS,
    run_package_checks,
)
from src.verify.metrics import compute_metrics
from src.verify.reverify import diff_checks

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
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    use_llm = st.toggle("LLM 문서 이해·쟁점 생성", value=has_key)
    live_law = st.toggle("국가법령정보 API 최신 원문 보강", value=bool(os.environ.get("LAW_API_OC")))
    # 이전 문구는 "키가 없으면 규칙 기반으로 자동 전환됩니다"였는데, 실측 결과
    # 규칙 폴백은 4개 문서를 전부 '상품설명서'로 분류해 쓸모 있는 판정이 0건이었다.
    # 지키지 못하는 약속을 화면에 두지 않는다.
    if not has_key:
        st.error("ANTHROPIC_API_KEY가 없습니다. 문서 분류·필드 추출 정확도가 크게 떨어집니다.")
    st.caption(
        "LLM을 끄면 규칙 기반으로만 동작합니다. 규칙 분류는 실물 서류에서 유형을 "
        "구분하지 못하는 경우가 많아 대부분의 항목이 '미확인'으로 남습니다."
    )
    st.divider()
    st.markdown("**MVP 검증 규칙**")
    st.code("PKG-001\nFIT-001\nEXP-001\nDATE-001\nACK-001", language=None)
    st.divider()
    st.markdown("**개인정보 처리**")
    st.caption(
        "업로드 서류는 판독을 위해 Anthropic API로 전송되며 모델 학습에 사용되지 않습니다. "
        "원본 파일은 디스크에 저장하지 않으나, 재호출 비용을 줄이기 위해 판독 결과를 "
        "로컬 `.cache/llm`에 남깁니다. 실제 고객 서류를 다룬 뒤에는 비워주세요."
    )
    if st.button("판독 캐시 비우기", use_container_width=True):
        removed = clear_llm_cache()
        st.success(f"캐시 {removed}건을 삭제했습니다.")

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

@st.cache_data(show_spinner=False, max_entries=64)
def process_document(raw_bytes: bytes, file_name: str, with_llm: bool, forced_type: str | None = None):
    """서류 1건 판독·추출. 파일 내용이 같으면 재실행하지 않는다.

    Streamlit은 위젯을 건드릴 때마다 스크립트를 처음부터 다시 돌린다. 캐시가 없으면
    페이지 선택 하나에도 전체 파이프라인이 재실행돼 실측 10초가 걸렸다(API 비용은
    결과 캐시 덕에 0이지만 로컬 재계산이 병목).
    """
    pdf = load_pdf(raw_bytes, document_id=file_name)
    parsed = to_parsed_document(pdf)
    result = extract_document(
        parsed, use_llm=with_llm, locator=pdf.locate, page_renderer=pdf.render_page,
        force_doc_type=forced_type,
    )
    return pdf, parsed.raw_text, result


@st.cache_data(show_spinner=False, max_entries=32)
def verify_package(document_payloads: tuple[str, ...], with_llm: bool):
    """패키지 판정·쟁점 생성. 문서 내용이 같으면 재실행하지 않는다."""
    documents = [ParsedDocument.model_validate_json(p) for p in document_payloads]
    package_checks = run_package_checks(documents)
    return package_checks, build_legal_issues(documents, package_checks, use_llm=with_llm)


@st.cache_data(show_spinner=False, max_entries=64)
def legal_basis(query: str, articles: tuple[str, ...], sources: tuple[str, ...], live: bool):
    return find_legal_basis(
        query, preferred_articles=articles, preferred_sources=sources,
        top_k=3, allow_live=live,
    )


def FIELD_SOURCE_LABEL(field) -> str:
    """추출값의 근거를 사람이 읽을 수 있게. 신뢰도는 추출 단계가 매긴 값이다.

    0.9 결정론적 스캔 / 0.85 이미지 판독 / 0.7 원문 대조·의미 매칭 / 0.0 폐기.
    """
    if not field.value:
        return "—"
    confidence = field.confidence or 0.0
    if confidence >= 0.9:
        return "규칙 확정"
    if confidence >= 0.8:
        return f"AI 이미지 판독{f' ({field.page}쪽)' if field.page else ''}"
    if confidence >= 0.5:
        return "원문 대조"
    return "AI 추출"


READ_ERROR_HINTS = {
    "EmptyFileError": "빈 파일입니다.",
    "FileDataError": "PDF가 손상되었거나 형식이 올바르지 않습니다.",
    "FzErrorFormat": "지원하지 않는 파일 형식입니다(PDF·JPG·PNG만 가능).",
}

started_at = time.perf_counter()
pdf_details = {}
pdf_bytes_map = {}
parsed_documents: list[ParsedDocument] = []
extraction_meta = {}
errors: list[str] = []
seen_names: set[str] = set()

progress = st.progress(0.0, text="서류를 판독하는 중…")
for index, file in enumerate(uploaded, start=1):
    # 같은 이름이 두 번 올라오면 dict 키가 겹쳐 한 건이 조용히 사라진다.
    name = file.name
    if name in seen_names:
        suffix = 2
        while f"{name} ({suffix})" in seen_names:
            suffix += 1
        name = f"{name} ({suffix})"
    seen_names.add(name)

    progress.progress((index - 1) / len(uploaded), text=f"[{index}/{len(uploaded)}] {name} 판독 중…")
    try:
        raw_bytes = file.getvalue()
        # 검토자가 앞선 실행에서 문서유형을 교정했다면 그 값으로 다시 추출한다.
        pdf, raw_text, result = process_document(
            raw_bytes, name, use_llm, st.session_state.get(f"doctype::{name}")
        )
        parsed_documents.append(ParsedDocument(
            document_id=name, doc_type=result.doc_type, fields=result.fields, raw_text=raw_text,
        ))
        pdf_details[name] = pdf
        pdf_bytes_map[name] = raw_bytes
        extraction_meta[name] = result
    except Exception as exc:
        hint = READ_ERROR_HINTS.get(type(exc).__name__, "")
        errors.append(f"{name}: {hint or f'{type(exc).__name__} - {exc}'}")
progress.empty()

if errors:
    st.error(
        f"{len(errors)}개 문서를 읽지 못했습니다. **아래 판정은 나머지 "
        f"{len(parsed_documents)}건만 반영합니다.**\n\n" + "\n\n".join(f"- {e}" for e in errors)
    )
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
        # 유형 하나가 틀리면 위험등급·날짜·고객확인 게이팅이 전부 어긋나 판정이
        # 조용히 약해진다. 실물 서류에서는 규칙 분류가 확신하지 못해(실측 22건 전부 침묵)
        # 유형 판단이 전적으로 LLM에 달려 있으므로, 검토자가 고칠 수 있어야 한다.
        options = list(DOC_TYPES)
        st.selectbox(
            "문서유형 (틀렸으면 교정)",
            options=options,
            index=options.index(document.doc_type) if document.doc_type in options else 0,
            format_func=lambda t: DOC_TYPES[t][0],
            key=f"doctype::{document.document_id}",
            help="교정하면 그 유형 기준으로 다시 추출·판정합니다.",
        )
        # 값이 있는 필드만 보여주면 같은 양식인데 목록이 달라 보인다.
        # 유형별 고정 필드를 전부 표시하고 못 찾은 것은 '미확인'으로 드러낸다.
        # 근거(신뢰도)도 함께 보여준다 — 규칙이 확정한 값과 AI가 그림에서 읽은 값이
        # 똑같이 생기면 검토자가 무엇을 더 확인해야 할지 알 수 없다.
        rows = [
            {
                "항목": field.name,
                "값": field.value or "미확인",
                "근거": FIELD_SOURCE_LABEL(field),
            }
            for field in document.fields
        ]
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.caption("추출된 핵심 필드 없음")

with st.spinner("패키지 교차 검증 중…"):
    checks, issues = verify_package(
        tuple(document.model_dump_json() for document in parsed_documents), use_llm
    )

st.subheader("2. 패키지 교차 검증·법령 근거")
summary_counts = {status: sum(check.status == status for check in checks) for status in CheckStatus}

# 숫자 4개만 보여주면 "그래서 이 판매건은 어떤 상태인가"에 답하지 못한다.
blockers = summary_counts[CheckStatus.RISK] + summary_counts[CheckStatus.MISSING]
if summary_counts[CheckStatus.RISK]:
    st.error(f"**판매 진행 부적합** — 위반 소지 {summary_counts[CheckStatus.RISK]}건이 확인됐습니다. 아래 근거를 검토하세요.")
elif blockers:
    st.warning(f"**추가 증빙 필요** — 확인하지 못한 항목 {blockers}건이 있습니다.")
elif summary_counts[CheckStatus.WARNING]:
    st.info(f"**조건부 적합** — 위반은 없으나 확인 권고 {summary_counts[CheckStatus.WARNING]}건이 있습니다.")
else:
    st.success("**검사 항목 전부 적합** — 아래 검사 범위 내에서 문제가 발견되지 않았습니다.")

# 검사 범위를 밝히지 않으면 '통과'가 '금소법 준수'로 읽힌다.
st.caption(
    "검사 범위: **금융소비자보호법 제17조(적합성원칙)·제19조(설명의무)** 관련 5개 항목. "
    "적정성(18조)·불공정영업(20조)·부당권유(21조)·광고(22조)는 이 도구의 검사 대상이 아닙니다."
)
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
        # 설명이 LLM이 쓴 것인지 미리 정해둔 폴백 문구인지 밝힌다(같은 자리에 성격이 다른 두 가지가 온다).
        source_label = "AI 쟁점 설명" if issue.used_llm else "규칙 기반 설명(LLM 미사용)"
        st.markdown(f"**{source_label}:** {html.escape(issue.rationale)}")
        st.info(issue.recommended_action)

        hint = LAW_HINTS[check.rule_id]
        legal_results = legal_basis(
            issue.search_query, hint.preferred_articles, hint.preferred_sources, live_law
        )
        # 근거 조문을 판정 객체에 실어둔다. 화면에서만 존재하면 결과를 내보내는 순간
        # 근거가 사라진다(스키마가 evidence_clause를 약속해두고 아무도 채우지 않았다).
        if legal_results:
            check.evidence_clause = legal_results[0].citation
            check.evidence_text = legal_results[0].text[:700]
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

st.subheader("3. 정량 지표")
# 캐시 도입 후 재실행 시간은 0에 가깝다. 지표에는 '첫 처리 시간'을 유지해야
# 수작업 대비 절감이 정직한 숫자가 된다(위젯을 누를 때마다 0.1초로 바뀌면 안 된다).
package_key = "|".join(sorted(document.document_id for document in parsed_documents))
first_elapsed = st.session_state.setdefault(
    f"elapsed::{package_key}", time.perf_counter() - started_at
)
metrics = compute_metrics(parsed_documents, checks, first_elapsed)
saved_seconds = max(metrics.manual_baseline_seconds - metrics.elapsed_seconds, 0)
metric_row = st.columns(4)
metric_row[0].metric("검증 문서", f"{metrics.document_count}건")
metric_row[1].metric("검사 항목", f"{metrics.check_count}개")
metric_row[2].metric("차단(위험·누락)", f"{metrics.blocker_count}건")
metric_row[3].metric("처리 시간", f"{metrics.elapsed_seconds:.1f}초")
st.caption(
    f"수작업 기준 {metrics.manual_baseline_seconds / 60:.0f}분 대비 "
    f"{saved_seconds / 60:.1f}분 단축 (문서당 15분 가정). "
    "판정은 결정론적 규칙이 내리므로 같은 서류·같은 정책이면 언제나 같은 결과입니다."
)

st.subheader("4. 규정 개정 재검증")
st.caption("규정이 개정되면 같은 서류의 판정이 달라질 수 있습니다. 정책을 바꿔 즉시 재검증합니다.")
policy_cols = st.columns(len(DEFAULT_PROFILE_MIN_ALLOWED_GRADE))
revised_policy = {}
for col, (profile_name, minimum) in zip(policy_cols, DEFAULT_PROFILE_MIN_ALLOWED_GRADE.items()):
    revised_policy[profile_name] = col.number_input(
        f"{profile_name} 최소 허용등급", min_value=1, max_value=6, value=int(minimum),
        key=f"policy_{profile_name}",
    )
if revised_policy != dict(DEFAULT_PROFILE_MIN_ALLOWED_GRADE):
    revised_checks = run_package_checks(parsed_documents, profile_min_grade=revised_policy)
    diff = diff_checks(checks, revised_checks)
    st.info(diff.summary_line())
    for check in diff.added:
        st.error(f"신규 위반 · {check.rule_id} · {check.description}")
    for check in diff.resolved:
        st.success(f"해소 · {check.rule_id} · {check.description}")
    for before, after in diff.changed:
        label_before = STATUS_LABEL[before.status][0]
        label_after = STATUS_LABEL[after.status][0]
        st.warning(f"상태 변화 · {before.rule_id} · {label_before} → {label_after}")
else:
    st.caption("현행 정책 기준입니다. 위 값을 바꾸면 개정 전/후 판정 차이를 즉시 보여줍니다.")

st.subheader("5. 서류 원문 하이라이트")
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
        # 좌표 미발견은 대개 '추출값이 원문에 없다'는 뜻이다(환각·OCR 오독).
        # 원인을 감추지 않고 드러내야 검증 도구로서 신뢰할 수 있다.
        st.warning(
            f"'{selected_value}'이(가) 이 문서 원문에서 발견되지 않았습니다. "
            "추출 오류(문서에 없는 값) 또는 OCR 오독일 수 있으니 원본을 확인하세요."
        )

st.subheader("6. 검증 결과 내보내기")
st.caption(
    "컴플라이언스 기록물은 '언제·어떤 기준으로 판정했는가'가 핵심입니다. "
    "판정·근거 조문과 함께 검증 시각·사용 모델·적용 정책을 담아 내려받습니다."
)
report = {
    "verified_at": datetime.now().isoformat(timespec="seconds"),
    "tool": "KB 금융상품 판매서류 검증 AI Copilot (MVP)",
    "scope": "금융소비자보호법 제17조(적합성원칙)·제19조(설명의무) 관련 5개 항목",
    "settings": {
        "llm_used": use_llm,
        "extraction_model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        "vision_model": os.environ.get("VISION_MODEL", "claude-haiku-4-5"),
        "live_law_lookup": live_law,
        "profile_min_grade": dict(DEFAULT_PROFILE_MIN_ALLOWED_GRADE),
    },
    "documents": [
        {
            "document_id": document.document_id,
            "doc_type": document.doc_type,
            "fields": [
                {
                    "name": field.name,
                    "value": field.value,
                    "page": field.page,
                    "source": FIELD_SOURCE_LABEL(field),
                }
                for field in document.fields
            ],
        }
        for document in parsed_documents
    ],
    "checks": [
        {
            "rule_id": check.rule_id,
            "description": check.description,
            "status": check.status.value,
            "document_excerpt": check.document_excerpt,
            "evidence_clause": check.evidence_clause,
            "suggestion": check.suggestion,
        }
        for check in checks
    ],
    "metrics": {
        "document_count": metrics.document_count,
        "blocker_count": metrics.blocker_count,
        "warning_count": metrics.warning_count,
        "elapsed_seconds": metrics.elapsed_seconds,
    },
    "unread_documents": errors,
}
st.download_button(
    "검증 결과 JSON 내려받기",
    data=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
    file_name=f"검증결과_{datetime.now():%Y%m%d_%H%M%S}.json",
    mime="application/json",
)

st.caption("주의: 이 MVP는 법률 위반을 확정하지 않으며, 규정 준수 여부의 추가 검토가 필요한 지점을 선별합니다.")
