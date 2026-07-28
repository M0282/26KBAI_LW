"""KB 펀드 판매서류 검증 AI Copilot — KB 서식 전용 버전.

범용 버전(app/main.py)과의 차이:
  1. 문서 유형을 KB 실제 서식 4종으로 본다.
     범용 분류는 '상품설명서' 하나로 뭉치는데, KB는 운용사 발행 간이투자설명서와
     판매사 발행 핵심[요약] 상품설명서가 따로 있다(실측).
  2. 분류에 LLM을 쓰지 않는다. KB 서식은 고정 양식이라 고유 문구만으로 구분된다
     (실측 9/9 정확). 분류 단계의 모델 변덕이 사라진다.
  3. KB라서 가능한 검사가 붙는다 — 운용사 위험등급 ↔ 당행부여 위험등급 일치
     (감독규정 12조). 두 문서를 구분하지 못하면 할 수 없는 검사다.

실행: py -3 -m streamlit run app/main_kb.py
"""
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

load_dotenv(ROOT / ".env", override=False)

from src.common.demo_store import available_count, load_result
from src.common.llm_cache import clear_llm_cache
from src.common.schemas import CheckStatus, ParsedDocument
from src.ingest.law_search import find_legal_basis
from src.parser.financial_extractor import extract_document, field_map
from src.parser.kb_documents import FORM_BY_CODE, KB_FORMS, classify_kb_form
from src.parser.pdf_loader import load_pdf, to_parsed_document
from src.parser.pdf_render import render_highlighted_page
from src.verify.ai_reasoner import build_legal_issues
from src.verify.financial_rules import LAW_HINTS, run_package_checks
from src.verify.kb_rules import KB_LAW_HINTS, check_grade_agreement, check_kb_document_set
from src.verify.metrics import compute_metrics

KB_YELLOW, KB_YELLOW_ALT, KB_GRAY = "#FCAF17", "#FDB913", "#645B4C"
STATUS_LABEL = {
    CheckStatus.PASS: ("통과", "#2E7D32"),
    CheckStatus.WARNING: ("주의", "#B26A00"),
    CheckStatus.MISSING: ("누락", "#C62828"),
    CheckStatus.RISK: ("위험", "#C62828"),
}
ALL_HINTS = {**LAW_HINTS, **KB_LAW_HINTS}

st.set_page_config(page_title="KB 펀드 판매서류 검증 AI Copilot", page_icon="🛡️", layout="wide")
st.markdown(
    f"""
<style>
.stApp {{ background:linear-gradient(180deg,#fffdf7 0%,#f7f6f2 100%); }}
.block-container {{ padding-top:1.25rem; max-width:1500px; }}
.kb-hero {{ background:white; border:1px solid #eee8da; border-radius:22px; padding:24px 28px;
box-shadow:0 10px 30px rgba(100,91,76,.08); margin-bottom:16px; }}
.kb-title {{ color:{KB_GRAY}; font-size:2.0rem; font-weight:800; margin:0; }}
.kb-title b {{ color:{KB_YELLOW}; }}
.kb-sub {{ color:#655f55; margin-top:8px; font-size:1.0rem; }}
.kb-badge {{ display:inline-block; border:1px solid {KB_YELLOW}; background:#fff8df; color:{KB_GRAY};
padding:6px 12px; border-radius:999px; margin-top:12px; font-weight:700; font-size:.9rem; }}
.kb-card {{ background:white; border:1px solid #eee8da; border-radius:16px; padding:14px 16px;
box-shadow:0 6px 18px rgba(100,91,76,.06); }}
.kb-evidence {{ background:#fff8df; border-left:4px solid {KB_YELLOW}; padding:10px 12px; border-radius:8px; }}
div[data-testid="stFileUploader"] {{ background:white; padding:10px; border-radius:14px; border:1px dashed {KB_YELLOW}; }}
.stButton button {{ background:{KB_YELLOW}; color:#332c22; border:none; font-weight:800; border-radius:10px; }}
</style>
<div class="kb-hero">
  <div class="kb-title"><b>KB</b> 펀드 판매서류 검증 <b>AI Copilot</b></div>
  <div class="kb-sub">KB 서식 4종을 인식해 금소법 위반 소지를 판매 시점에 확인합니다.
  문서 분류는 서식 고유 문구로 수행하므로 모델과 무관하게 일정합니다.</div>
  <div class="kb-badge">KB 서식 전용 · 문서 간 교차 검증 · 근거 조문 제시</div>
</div>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("검증 설정")
    # 키는 .env 에서만 읽는다(화면에서 자격증명을 받지 않는다).
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    st.caption(f"LLM 판독: {'사용 가능 (.env 키 확인됨)' if has_key else '사용 불가 (.env에 키 없음)'}")
    live_law = st.toggle("국가법령정보 API 최신 원문 보강", value=bool(os.environ.get("LAW_API_OC")))
    st.divider()
    st.markdown("**인식하는 KB 서식**")
    for form in KB_FORMS:
        st.caption(f"**{form.label}** — {form.role}")
    st.divider()
    st.markdown("**검증 규칙 10종**")
    st.code(
        "KB-GRADE 운용사·당행 등급 일치 (감독규정 12조)\n"
        "KB-DOC   KB 서류 4종 구비 (23조)\n"
        "FIT-001  적합성 (17조)\n"
        "EXP-001  설명의무 (19조)\n"
        "DATE-001 설명-계약 선후 (19조)\n"
        "ACK-001  설명 확인 증빙 (19조)\n"
        "PKG-001  상품 동일성\n"
        "ADV-001  부당권유 금지 (21조)\n"
        "REC-001  녹취 의무 대상 (28조)",
        language=None,
    )
    st.divider()
    st.caption(
        "업로드 서류는 판독을 위해 Anthropic API로 전송되며 학습에 사용되지 않습니다. "
        "원본은 저장하지 않고, 판독 결과만 로컬 캐시에 남습니다."
    )
    if st.button("판독 캐시 비우기", use_container_width=True):
        st.success(f"캐시 {clear_llm_cache()}건을 삭제했습니다.")

DEMO_RESULTS = available_count()
if not has_key and not DEMO_RESULTS:
    st.error("**ANTHROPIC_API_KEY도, 사전 판독 결과도 없어 검증을 시작할 수 없습니다.**")
    st.code("프로젝트 루트의 .env 파일에\nANTHROPIC_API_KEY=sk-ant-...", language=None)
    st.stop()
if not has_key:
    st.info(
        f"**데모 모드** — API 키가 없어 사전 판독 결과({DEMO_RESULTS}건)를 재생합니다. "
        "판정은 평소와 동일하게 규칙이 수행합니다. "
        "다른 서류를 검증하려면 프로젝트 루트의 `.env` 파일에 "
        "`ANTHROPIC_API_KEY` 를 넣고 다시 실행하세요."
    )

st.markdown("#### 판매 건별 서류 업로드")
st.info(
    "**펀드 계약 한 건에 필요한 KB 서류 4종을 한 칸에 올려주세요.**\n\n"
    "① **적합성 진단표** (KB스타뱅킹 투자성향분석 캡처) ② **간이투자설명서** (운용사 발행) "
    "③ **핵심[요약] 상품설명서** (고객교부용) ④ **집합투자증권 계약서**\n\n"
    "서식은 문서 고유 문구로 자동 인식하며, 여러 계약은 **판매 건 추가**로 칸을 늘리세요."
)

st.session_state.setdefault("kb_package_count", 1)
uploaded_packages: list[list] = []
for slot in range(st.session_state.kb_package_count):
    with st.container(border=True):
        files = st.file_uploader(
            f"판매 건 {slot + 1} — KB 서류 4종 (PDF · JPG · PNG)",
            type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True,
            key=f"kb_upload_{slot}",
        )
        st.checkbox("이 건의 고객은 고령투자자(만 65세 이상)입니다", key=f"kb_elderly_{slot}")
        uploaded_packages.append(list(files or []))

add_col, remove_col, _ = st.columns([1, 1, 4])
if add_col.button("＋ 판매 건 추가", use_container_width=True):
    st.session_state.kb_package_count += 1
    st.rerun()
if st.session_state.kb_package_count > 1 and remove_col.button("－ 마지막 칸 제거", use_container_width=True):
    st.session_state.pop(f"kb_upload_{st.session_state.kb_package_count - 1}", None)
    st.session_state.kb_package_count -= 1
    st.rerun()

if not any(uploaded_packages):
    left, center, right = st.columns(3)
    for col, (head, body) in zip(
        (left, center, right),
        [("① KB 서식 인식", "고유 문구로 4종을 구분합니다. 분류에 LLM을 쓰지 않아 항상 같은 결과입니다."),
         ("② 값 추출", "위험등급·투자성향·계약일·서명을 읽고, 원문에 없는 값은 버립니다."),
         ("③ 교차 검증", "서류 사이의 불일치를 규칙으로 판정하고 근거 조문을 제시합니다.")],
    ):
        col.markdown(f'<div class="kb-card"><h4>{head}</h4><p>{body}</p></div>', unsafe_allow_html=True)
    st.stop()

if not has_key:
    unknown = sorted({f.name for files in uploaded_packages for f in files
                      if load_result(f.getvalue()) is None})
    if unknown:
        st.error(
            "**이 서류는 데모 모드에서 검증할 수 없습니다.**\n\n"
            + "\n".join(f"- {n}" for n in unknown)
            + "\n\n프로젝트 루트의 `.env` 파일에 `ANTHROPIC_API_KEY` 를 넣고 "
            "앱을 다시 실행하시면 검증할 수 있습니다."
        )
        st.stop()


@st.cache_data(show_spinner=False, max_entries=64)
def read_document(raw_bytes: bytes, file_name: str, with_llm: bool):
    """KB 서식을 인식하고 값을 추출한다. 서식 분류는 LLM을 쓰지 않는다."""
    pdf = load_pdf(raw_bytes, document_id=file_name)
    parsed = to_parsed_document(pdf)
    form_code = classify_kb_form(pdf.text)
    canonical = FORM_BY_CODE[form_code].canonical if form_code else None
    if not with_llm:
        stored = load_result(raw_bytes)
        if stored is not None:
            return pdf, parsed.raw_text, stored, form_code
    result = extract_document(
        parsed, use_llm=with_llm, locator=pdf.locate,
        page_renderer=pdf.render_page, force_doc_type=canonical,
    )
    return pdf, parsed.raw_text, result, form_code


@st.cache_data(show_spinner=False, max_entries=64)
def legal_basis(query: str, articles: tuple[str, ...], sources: tuple[str, ...], live: bool):
    return find_legal_basis(query, preferred_articles=articles,
                            preferred_sources=sources, top_k=3, allow_live=live)


def source_label(field) -> str:
    if not field.value:
        return "—"
    c = field.confidence or 0.0
    if c >= 0.9:
        return "규칙 확정"
    if c >= 0.8:
        return f"AI 이미지 판독{f' ({field.page}쪽)' if field.page else ''}"
    return "원문 대조" if c >= 0.5 else "AI 추출"


started_at = time.perf_counter()
packages = []
progress = st.progress(0.0, text="서류를 판독하는 중…")
active = [i for i, f in enumerate(uploaded_packages) if f]
for order, slot in enumerate(active, start=1):
    progress.progress((order - 1) / len(active), text=f"판매 건 {slot + 1} 판독 중…")
    documents, forms, pdfs, raws, metas, errors = [], {}, {}, {}, {}, []
    for file in uploaded_packages[slot]:
        try:
            raw = file.getvalue()
            pdf, text, result, form_code = read_document(raw, file.name, has_key)
            doc = ParsedDocument(document_id=file.name, doc_type=result.doc_type,
                                 fields=result.fields, raw_text=text)
            documents.append(doc)
            pdfs[file.name], raws[file.name], metas[file.name] = pdf, raw, (result, form_code)
            if form_code:
                forms[form_code] = doc
        except Exception as exc:
            errors.append(f"{file.name}: {type(exc).__name__}")
    if not documents:
        continue
    elderly = bool(st.session_state.get(f"kb_elderly_{slot}"))
    checks = [check_kb_document_set(set(forms)), check_grade_agreement(forms)]
    checks += [c for c in run_package_checks(documents, elderly_investor=elderly)
               if c.rule_id != "DOC-001"]   # KB-DOC로 대체
    packages.append({"slot": slot, "label": f"판매 건 {slot + 1}", "documents": documents,
                     "forms": forms, "pdfs": pdfs, "raws": raws, "metas": metas,
                     "checks": checks, "errors": errors, "elderly": elderly})
progress.empty()

if not packages:
    st.error("읽을 수 있는 서류가 없습니다.")
    st.stop()

if len(packages) > 1:
    st.subheader("판매 건 요약")
    rows = []
    for pkg in packages:
        counts = {s: sum(c.status == s for c in pkg["checks"]) for s in CheckStatus}
        verdict = ("판매 진행 부적합" if counts[CheckStatus.RISK] else
                   "추가 증빙 필요" if counts[CheckStatus.MISSING] else
                   "조건부 적합" if counts[CheckStatus.WARNING] else "적합")
        product = next((v for d in pkg["documents"] if (v := field_map(d).get("product_name"))), "상품 미상")
        rows.append({"판매 건": pkg["label"], "상품": product, "종합 판정": verdict,
                     "고령투자자": "예" if pkg["elderly"] else "—",
                     "위험": counts[CheckStatus.RISK], "누락": counts[CheckStatus.MISSING],
                     "주의": counts[CheckStatus.WARNING], "통과": counts[CheckStatus.PASS]})
    st.dataframe(rows, hide_index=True, use_container_width=True)
    chosen = st.selectbox("상세를 볼 판매 건", [p["label"] for p in packages], key="kb_selected")
    package = next(p for p in packages if p["label"] == chosen)
    st.markdown(f"#### {package['label']} 상세")
else:
    package = packages[0]

documents, checks = package["documents"], package["checks"]
if package["errors"]:
    st.error("일부 문서를 읽지 못했습니다.\n\n" + "\n".join(f"- {e}" for e in package["errors"]))

st.subheader("1. KB 서식 인식·핵심 값 추출")
cols = st.columns(min(len(documents), 4))
for i, doc in enumerate(documents):
    result, form_code = package["metas"][doc.document_id]
    form = FORM_BY_CODE.get(form_code)
    pdf = package["pdfs"][doc.document_id]
    read_mode = ("AI 비전 판독" if pdf.vision_applied else
                 "OCR" if pdf.ocr_applied else "텍스트 레이어")
    with cols[i % len(cols)]:
        st.markdown(
            f'<div class="kb-card"><b>{html.escape(doc.document_id[:30])}</b><br>'
            f'<span style="color:{KB_YELLOW_ALT};font-weight:800">'
            f'{form.label if form else "KB 서식 아님"}</span><br>'
            f'<small>{form.role if form else "서식을 인식하지 못했습니다"}</small><br>'
            f'<small>판독: {read_mode}</small></div>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            [{"항목": f.name, "값": f.value or "미확인", "근거": source_label(f)} for f in doc.fields],
            hide_index=True, use_container_width=True,
        )

st.subheader("2. 교차 검증·법령 근거")
counts = {s: sum(c.status == s for c in checks) for s in CheckStatus}
blockers = counts[CheckStatus.RISK] + counts[CheckStatus.MISSING]
if counts[CheckStatus.RISK]:
    st.error(f"**판매 진행 부적합** — 위반 소지 {counts[CheckStatus.RISK]}건")
elif blockers:
    st.warning(f"**추가 증빙 필요** — 확인하지 못한 항목 {blockers}건")
elif counts[CheckStatus.WARNING]:
    st.info(f"**조건부 적합** — 확인 권고 {counts[CheckStatus.WARNING]}건")
else:
    st.success("**검사 항목 전부 적합**")
st.caption(
    "검사 범위: 금융소비자보호법 제17조(적합성)·제19조(설명의무)·제21조(부당권유)·"
    "제23조(계약서류)·제28조(기록유지) 및 감독규정 제12조(위험등급). "
    "적정성(18조)·불공정영업(20조)·광고(22조)는 판매서류만으로 판단할 수 없어 검사 대상이 아닙니다."
)

metric_cols = st.columns(4)
for col, status in zip(metric_cols, [CheckStatus.PASS, CheckStatus.WARNING,
                                     CheckStatus.MISSING, CheckStatus.RISK]):
    col.metric(STATUS_LABEL[status][0], counts[status])

issues = build_legal_issues(documents, checks, use_llm=has_key)
for check in checks:
    label, color = STATUS_LABEL[check.status]
    with st.expander(f"[{label}] {check.rule_id} · {check.description}",
                     expanded=check.status != CheckStatus.PASS):
        st.markdown(f"**판정:** <span style='color:{color};font-weight:800'>{label}</span>",
                    unsafe_allow_html=True)
        if check.document_excerpt:
            st.markdown(f'<div class="kb-evidence"><b>서류 근거</b><br>'
                        f'{html.escape(check.document_excerpt)}</div>', unsafe_allow_html=True)
        issue = issues.get(check.rule_id)
        if issue:
            st.markdown(f"**{'AI 쟁점 설명' if issue.used_llm else '규칙 기반 설명'}:** "
                        f"{html.escape(issue.rationale)}")
        if check.suggestion:
            st.info(check.suggestion)
        hint = ALL_HINTS.get(check.rule_id)
        if hint:
            results = legal_basis(issue.search_query if issue else hint.query,
                                  hint.preferred_articles, hint.preferred_sources, live_law)
            if results:
                check.evidence_clause = results[0].citation
                st.markdown("**관련 법령 원문** (검색 상위 3건)")
                for rank, r in enumerate(results, start=1):
                    st.markdown(f"- `{'최우선 근거' if rank == 1 else f'참고 {rank}'}` "
                                f"**{r.citation}** · {r.title}")
                    if r.text:
                        with st.expander(f"{r.citation} 원문 보기"):
                            st.caption(r.text[:700])

st.subheader("3. 정량 지표")
key = "|".join(sorted(d.document_id for d in documents))
elapsed = st.session_state.setdefault(f"kb_elapsed::{key}", time.perf_counter() - started_at)
metrics = compute_metrics(documents, checks, elapsed)
m = st.columns(4)
m[0].metric("검증 문서", f"{metrics.document_count}건")
m[1].metric("검사 항목", f"{metrics.check_count}개")
m[2].metric("차단(위험·누락)", f"{metrics.blocker_count}건")
m[3].metric("처리 시간", f"{metrics.elapsed_seconds:.1f}초")
st.caption(f"수작업 기준 {metrics.manual_baseline_seconds / 60:.0f}분 대비 "
           f"{max(metrics.manual_baseline_seconds - metrics.elapsed_seconds, 0) / 60:.1f}분 단축 "
           "(문서당 15분 가정). 판정은 결정론적 규칙이 수행하므로 같은 서류·같은 정책이면 결과가 같습니다.")

st.subheader("4. 서류 원문 하이라이트")
target = st.selectbox("문서 선택", [d.document_id for d in documents], key="kb_hl_doc")
selected = next(d for d in documents if d.document_id == target)
values = [f for f in selected.fields if f.value and f.value != "확인"]
if not values:
    st.caption("표시할 추출값이 없습니다.")
else:
    labels = [f"{f.name} · {f.value}" for f in values]
    field = values[labels.index(st.selectbox("찾을 값", labels, key=f"kb_hl_v::{target}"))]
    pdf = package["pdfs"][target]
    hits = pdf.locate(field.value)
    if hits:
        pages = [h["page"] for h in hits]
        page = (st.radio("페이지", pages, horizontal=True, format_func=lambda p: f"{p}쪽",
                         key=f"kb_hl_p::{target}::{field.value}") if len(pages) > 1 else pages[0])
        hit = next(h for h in hits if h["page"] == page)
        st.image(render_highlighted_page(package["raws"][target], page_number=hit["page"],
                                         rects=hit["rects"]),
                 caption=f"{target} · {hit['page']}쪽 · '{field.value}'", use_container_width=True)
    elif field.page:
        st.info(f"'{field.value}'은(는) **{field.page}쪽 이미지에서 판독**한 값입니다(좌표 없음).")
        st.image(render_highlighted_page(package["raws"][target], page_number=field.page, rects=[]),
                 caption=f"{target} · {field.page}쪽", use_container_width=True)
    else:
        st.warning(f"'{field.value}'을(를) 원문에서 찾지 못했습니다.")

st.subheader("5. 검증 결과 내보내기")
report = {
    "verified_at": datetime.now().isoformat(timespec="seconds"),
    "tool": "KB 펀드 판매서류 검증 AI Copilot (KB 서식 전용)",
    "scope": "금소법 17·19·21·23·28조 및 감독규정 12조",
    "elderly_investor": package["elderly"],
    "documents": [
        {"document_id": d.document_id,
         "kb_form": (FORM_BY_CODE[package["metas"][d.document_id][1]].label
                     if package["metas"][d.document_id][1] else None),
         "fields": [{"name": f.name, "value": f.value, "page": f.page,
                     "source": source_label(f)} for f in d.fields]}
        for d in documents
    ],
    "checks": [{"rule_id": c.rule_id, "description": c.description, "status": c.status.value,
                "document_excerpt": c.document_excerpt, "evidence_clause": c.evidence_clause,
                "suggestion": c.suggestion} for c in checks],
}
st.download_button("검증 결과 JSON 내려받기",
                   data=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                   file_name=f"KB검증결과_{datetime.now():%Y%m%d_%H%M%S}.json",
                   mime="application/json")

st.caption("주의: 이 도구는 법률 위반을 확정하지 않으며, 추가 검토가 필요한 지점을 선별합니다.")
