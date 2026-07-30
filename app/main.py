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


def _running_under_streamlit() -> bool:
    """`streamlit run` 으로 실행됐는지. 아니면 화면이 뜨지 않는다.

    streamlit.runtime.exists() 로는 판별할 수 없다 — 1.60의 uvicorn 기반
    서버에서는 스크립트 실행 중에도 False가 나와서, 그걸 믿고 종료하면
    앱 전체가 500으로 죽는다(실측). 스크립트 컨텍스트 유무로 판별한다.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError:  # 내부 API라 향후 옮겨질 수 있다 — 그때는 검사를 건너뛴다.
        return True
    return get_script_run_ctx() is not None


# `python app/main.py` 로 실행하면 streamlit이 경고 수십 줄만 쏟아내고 화면은
# 뜨지 않는다. 무엇을 잘못했는지 알기 어려우므로 여기서 먼저 알려준다.
if not _running_under_streamlit():
    print(
        "\n이 파일은 Streamlit 앱이라 `python` 으로는 실행되지 않습니다.\n"
        "\n  Windows : run.bat 을 더블클릭하세요 (가장 간단합니다)\n"
        "  직접 실행: streamlit run app/main.py\n"
        "\n자세한 안내는 실행안내.md 를 보세요.\n"
    )
    raise SystemExit(1)

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
    focus_pattern,
    focused_law_paragraphs,
    run_package_checks,
    split_law_paragraphs,
)
from src.verify.metrics import compute_metrics

KB_YELLOW = "#FCAF17"
KB_YELLOW_ALT = "#FDB913"
KB_GRAY = "#645B4C"
STATUS_LABEL = {
    CheckStatus.PASS: ("통과", "#2E7D32"),
    CheckStatus.WARNING: ("주의", "#B26A00"),
    CheckStatus.MISSING: ("누락", "#C62828"),
    CheckStatus.RISK: ("위험", "#C62828"),
}

def _highlight_law(paragraph: str, focus) -> str:
    """조문 문장에서 규칙이 걸리는 문구를 형광 표시한다.

    법령 원문은 그대로 보여야 하므로 먼저 이스케이프하고, 그 다음 강조 표시만
    입힌다(원문에 <, & 가 드물지만 넣지 않을 이유가 없다).
    """
    escaped = html.escape(paragraph)
    pattern = focus_pattern(focus)
    if not pattern:
        return escaped
    # 이스케이프 후 위치가 달라질 수 있는 문자는 강조 문구에 쓰지 않는다(한글·기호뿐).
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped)


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
.kb-law {{ background:#fcfbf8; border:1px solid #eee8da; border-left:4px solid {KB_GRAY};
padding:10px 14px; border-radius:8px; margin:6px 0 10px; }}
.kb-law p {{ margin:0 0 8px; font-size:0.9rem; line-height:1.62; color:#3a3630; }}
.kb-law p:last-child {{ margin-bottom:0; }}
.kb-law mark {{ background:{KB_YELLOW}; color:#241f18; padding:1px 2px; border-radius:3px; font-weight:700; }}
.kb-law-full p {{ font-size:0.83rem; color:#5a544b; }}
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
    # LLM을 끈 채로도 화면은 멀쩡히 뜨지만 판정은 전부 '미확인'이 된다(실측: 유효 판정 0건).
    # 조용히 틀린 결과를 보여주느니 아예 막는다.
    use_llm = st.toggle("LLM 문서 이해·쟁점 생성", value=has_key, disabled=not has_key)
    live_law = st.toggle("국가법령정보 API 최신 원문 보강", value=bool(os.environ.get("LAW_API_OC")))
    # 이전 문구는 "키가 없으면 규칙 기반으로 자동 전환됩니다"였는데, 실측 결과
    # 규칙 폴백은 4개 문서를 전부 '상품설명서'로 분류해 쓸모 있는 판정이 0건이었다.
    # 지키지 못하는 약속을 화면에 두지 않는다.
    st.caption(
        "LLM 없이는 실물 서류의 문서유형·필드를 신뢰할 수 있게 추출하지 못해 "
        "대부분의 항목이 '미확인'으로 남습니다. 잘못된 판정을 내놓는 대신 검증을 중단합니다."
    )
    st.divider()
    st.markdown("**MVP 검증 규칙**")
    st.code(
        "PKG-001  상품 동일성\n"
        "FIT-001  적합성 (17조)\n"
        "EXP-001  설명의무 (19조)\n"
        "DATE-001 설명-계약 선후 (19조)\n"
        "ACK-001  설명 확인 증빙 (19조)\n"
        "ADV-001  부당권유 금지 (21조)\n"
        "DOC-001  서류 구비 (23조)\n"
        "REC-001  녹취 의무 대상 (28조)",
        language=None,
    )
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

if not has_key:
    # 키가 없으면 업로드 자체를 막는다. 화면은 뜨지만 판정이 전부 '미확인'이 되는
    # 상태로 시연하면 도구를 신뢰할 수 없다.
    st.error(
        "**ANTHROPIC_API_KEY가 설정되지 않아 검증을 시작할 수 없습니다.**\n\n"
        "이 도구는 비정형 서류에서 판정에 필요한 값을 읽기 위해 LLM 판독이 필요합니다. "
        "키 없이도 화면은 뜨지만 문서유형·필드를 신뢰할 수 있게 추출하지 못해 "
        "모든 항목이 '미확인'으로 남습니다. 잘못된 판정을 내놓는 대신 중단합니다."
    )
    st.code("프로젝트 루트의 .env 파일에\nANTHROPIC_API_KEY=sk-ant-...", language=None)
    st.stop()

st.markdown("#### 판매서류 업로드")
st.info(
    "**한 판매 건(상품 계약 1개)마다 그 칸에 서류 4종을 올려주세요.**\n\n"
    "① **적합성 진단표** — 고객 투자성향 ② **상품설명서**(투자설명서) — 상품 위험등급 "
    "③ **가입신청서**(계약서) — 계약일·서명 ④ **설명 확인서** — 설명 이행 확인\n\n"
    "서류가 빠지면 그 항목은 검증할 수 없어 '누락'으로 표시됩니다. "
    "**비대면(모바일) 가입은 ④ 설명 확인서가 별도 파일로 없습니다** — 아래 체크박스를 "
    "표시하면 계약서의 확인 문구와 전자서명을 증빙으로 인정합니다.\n\n"
    "여러 계약을 검증하려면 아래 **판매 건 추가**로 칸을 늘리세요 — 칸별로 따로 판정합니다."
)

# 판매 건을 칸으로 나눠 받는다.
# 예전에는 한 칸에 전부 받고 상품명으로 자동 분리했는데, 적합성 진단표처럼 상품 식별
# 정보가 없는 서류는 어느 계약 것인지 알 수 없어 모든 계약에 붙었다(실측: 서로 다른
# 금융사 진단표 2건이 양쪽 판매 건에 배정됨 → 임의의 투자성향으로 판정될 위험).
# 사용자가 직접 나누면 추측이 사라지고, 수정도 해당 칸의 파일만 교체하면 된다.
st.session_state.setdefault("package_count", 1)

uploaded_packages: list[list] = []
for slot in range(st.session_state.package_count):
    with st.container(border=True):
        files = st.file_uploader(
            f"판매 건 {slot + 1} — 서류 4종 (PDF · JPG · PNG)",
            type=["pdf", "jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key=f"pkg_upload_{slot}",
            help="PDF가 가장 정확하며, 스캔·사진·스크린샷(JPG/PNG)은 자동 OCR로 인식합니다. "
            "OCR이 흐릿한 사진·다크모드 화면을 못 읽으면 AI 비전 판독으로 자동 전환합니다.",
        )
        # 고령 여부는 고객마다 다르다. 생년월일은 개인정보라 추출하지 않으므로
        # 판매 건마다 검토자가 직접 표시한다(전역 설정이면 다른 고객에게도 적용된다).
        st.checkbox(
            "이 건의 고객은 고령투자자(만 65세 이상)입니다",
            key=f"elderly_{slot}",
            help="해당하면 고위험 상품이 아니어도 녹취 의무 대상일 수 있습니다.",
        )
        # 판매 채널도 서류에서 읽히지 않는다. 비대면은 설명확인서가 별도 파일로
        # 존재하지 않고 계약서의 확인 문구 + 전자서명이 그 역할을 하므로,
        # 표시된 건에서만 '서류 누락'을 '확인 사항'으로 낮춘다.
        st.checkbox(
            "이 건은 비대면(모바일·인터넷) 가입입니다",
            key=f"nonface_{slot}",
            help="비대면은 설명 확인서가 별도 파일로 없고 계약서의 확인 문구와 전자서명이 "
            "그 역할을 합니다. 체크하면 설명 확인서가 없어도 '누락'이 아니라 '주의'로 "
            "표시하고, 전자문서함 기록을 함께 확인하도록 안내합니다. "
            "영업점 판매라면 체크하지 마세요 — 그때는 확인서가 실제로 있어야 합니다.",
        )
        uploaded_packages.append(list(files or []))

add_col, remove_col, _ = st.columns([1, 1, 4])
if add_col.button("＋ 판매 건 추가", use_container_width=True):
    st.session_state.package_count += 1
    st.rerun()
if st.session_state.package_count > 1 and remove_col.button(
    "－ 마지막 칸 제거", use_container_width=True
):
    st.session_state.pop(f"pkg_upload_{st.session_state.package_count - 1}", None)
    st.session_state.package_count -= 1
    st.rerun()

if not any(uploaded_packages):
    left, center, right = st.columns(3)
    with left:
        st.markdown('<div class="kb-card"><h3>① 판매 건별 업로드</h3><p>상품 계약 하나에 필요한 서류 4종을 한 칸에 올립니다.</p></div>', unsafe_allow_html=True)
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
def verify_package(document_payloads: tuple[str, ...], with_llm: bool, elderly: bool = False,
                   non_face_to_face: bool = False):
    """패키지 판정·쟁점 생성. 문서 내용이 같으면 재실행하지 않는다."""
    documents = [ParsedDocument.model_validate_json(p) for p in document_payloads]
    package_checks = run_package_checks(
        documents, elderly_investor=elderly, non_face_to_face=non_face_to_face
    )
    return package_checks, build_legal_issues(documents, package_checks, use_llm=with_llm)


@st.cache_data(show_spinner=False, max_entries=64)
def legal_basis(query: str, articles: tuple[str, ...], sources: tuple[str, ...], live: bool,
                focus: tuple[str, ...] = (), basis: tuple[tuple[str, str], ...] = ()):
    return find_legal_basis(
        query, preferred_articles=articles, preferred_sources=sources,
        top_k=3, allow_live=live, focus=focus, basis=basis,
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


def process_package(files: list, slot: int):
    """판매 건 한 칸의 파일들을 판독해 (문서목록, PDF, 원본bytes, 추출메타, 오류)를 돌려준다."""
    documents: list[ParsedDocument] = []
    pdfs: dict[str, object] = {}
    raw_map: dict[str, bytes] = {}
    meta: dict[str, object] = {}
    failures: list[str] = []
    seen: set[str] = set()

    for order, file in enumerate(files, start=1):
        # 같은 이름이 두 번 올라오면 dict 키가 겹쳐 한 건이 조용히 사라진다.
        name = file.name
        if name in seen:
            suffix = 2
            while f"{name} ({suffix})" in seen:
                suffix += 1
            name = f"{name} ({suffix})"
        seen.add(name)
        try:
            raw_bytes = file.getvalue()
            # 검토자가 앞선 실행에서 문서유형을 교정했다면 그 값으로 다시 추출한다.
            pdf, raw_text, result = process_document(
                raw_bytes, name, use_llm, st.session_state.get(f"doctype::{slot}::{name}")
            )
            documents.append(ParsedDocument(
                document_id=name, doc_type=result.doc_type, fields=result.fields, raw_text=raw_text,
            ))
            pdfs[name] = pdf
            raw_map[name] = raw_bytes
            meta[name] = result
        except Exception as exc:
            hint = READ_ERROR_HINTS.get(type(exc).__name__, "")
            failures.append(f"{name}: {hint or f'{type(exc).__name__} - {exc}'}")
    return documents, pdfs, raw_map, meta, failures


started_at = time.perf_counter()
active_slots = [slot for slot, files in enumerate(uploaded_packages) if files]
packages = []

progress = st.progress(0.0, text="서류를 판독하는 중…")
for order, slot in enumerate(active_slots, start=1):
    progress.progress(
        (order - 1) / len(active_slots), text=f"판매 건 {slot + 1} 판독 중… ({order}/{len(active_slots)})"
    )
    documents, pdfs, raw_map, meta, failures = process_package(uploaded_packages[slot], slot)
    if not documents and not failures:
        continue
    slot_checks, slot_issues = (
        verify_package(
            tuple(d.model_dump_json() for d in documents),
            use_llm,
            bool(st.session_state.get(f"elderly_{slot}")),
            bool(st.session_state.get(f"nonface_{slot}")),
        )
        if documents else ([], {})
    )
    packages.append({
        "slot": slot,
        "label": f"판매 건 {slot + 1}",
        "elderly": bool(st.session_state.get(f"elderly_{slot}")),
        "nonface": bool(st.session_state.get(f"nonface_{slot}")),
        "documents": documents,
        "pdfs": pdfs,
        "raw": raw_map,
        "meta": meta,
        "errors": failures,
        "checks": slot_checks,
        "issues": slot_issues,
    })
progress.empty()

if not packages:
    st.error("읽을 수 있는 서류가 없습니다.")
    st.stop()

# 여러 판매 건이면 요약을 먼저 보여주고, 하나를 골라 상세로 들어간다.
if len(packages) > 1:
    st.subheader("판매 건 요약")
    st.caption("칸별로 따로 판정한 결과입니다. 상세를 보려면 아래에서 판매 건을 선택하세요.")

    summary_rows = []
    for package in packages:
        counts = {s: sum(c.status == s for c in package["checks"]) for s in CheckStatus}
        if counts[CheckStatus.RISK]:
            verdict = "판매 진행 부적합"
        elif counts[CheckStatus.MISSING]:
            verdict = "추가 증빙 필요"
        elif counts[CheckStatus.WARNING]:
            verdict = "조건부 적합"
        else:
            verdict = "적합"
        product = next(
            (v for d in package["documents"] if (v := field_map(d).get("product_name"))), "상품 미상"
        )
        summary_rows.append({
            "판매 건": package["label"],
            "상품": product,
            "고령투자자": "예" if package["elderly"] else "—",
            "판매채널": "비대면" if package["nonface"] else "대면",
            "종합 판정": verdict,
            "위험": counts[CheckStatus.RISK],
            "누락": counts[CheckStatus.MISSING],
            "주의": counts[CheckStatus.WARNING],
            "통과": counts[CheckStatus.PASS],
            "서류": len(package["documents"]),
        })
    st.dataframe(summary_rows, hide_index=True, use_container_width=True)

    total_risk = sum(row["위험"] for row in summary_rows)
    if total_risk:
        st.error(f"판매 건 {len(packages)}개 중 위반 소지 **{total_risk}건**이 확인됐습니다.")
    else:
        st.success(f"판매 건 {len(packages)}개 모두 위반 소지가 발견되지 않았습니다.")

    st.divider()
    chosen = st.selectbox(
        "상세를 볼 판매 건",
        options=[p["label"] for p in packages],
        key="selected_package",
    )
    package = next(p for p in packages if p["label"] == chosen)
    st.markdown(f"#### {package['label']} 상세")
else:
    package = packages[0]
    st.success(f"{len(package['documents'])}개 문서를 하나의 판매 건으로 분석했습니다.")

parsed_documents = package["documents"]
pdf_details = package["pdfs"]
pdf_bytes_map = package["raw"]
extraction_meta = package["meta"]
errors = package["errors"]
checks = package["checks"]
issues = package["issues"]
active_slot = package["slot"]

if errors:
    st.error(
        f"{len(errors)}개 문서를 읽지 못했습니다. **아래 판정은 나머지 "
        f"{len(parsed_documents)}건만 반영합니다.**\n\n" + "\n\n".join(f"- {e}" for e in errors)
    )
if not parsed_documents:
    st.stop()


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
            key=f"doctype::{active_slot}::{document.document_id}",
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
    "검사 범위: **금융소비자보호법 제17조(적합성)·제19조(설명의무)·제21조(부당권유)·"
    "제23조(계약서류 제공)·제28조(기록 유지)** 관련 8개 항목. "
    "적정성(18조)·불공정영업(20조)·광고(22조)는 이 도구의 검사 대상이 아닙니다."
)
metric_cols = st.columns(4)
for col, status in zip(metric_cols, [CheckStatus.PASS, CheckStatus.WARNING, CheckStatus.MISSING, CheckStatus.RISK]):
    label, _ = STATUS_LABEL[status]
    col.metric(label, summary_counts[status])

# 규칙별 근거 조문을 모아 둔다. 화면과 내보내기가 같은 근거를 담아야 한다.
legal_basis_by_rule: dict[str, list[dict]] = {}

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
            issue.search_query, hint.preferred_articles, hint.preferred_sources, live_law,
            focus=hint.grounding, basis=hint.basis,
        )
        # 근거 조문을 판정 객체에 실어둔다. 화면에서만 존재하면 결과를 내보내는 순간
        # 근거가 사라진다(스키마가 evidence_clause를 약속해두고 아무도 채우지 않았다).
        #
        # 담는 것은 '규칙이 걸리는 항'이다. 예전에는 조문 앞 700자를 잘라 넣었는데,
        # 금소법 19조는 ①항(상품 유형별 설명 항목)만으로 700자를 넘어서 정작
        # 설명 확인 의무(②항)가 기록에서 빠졌다(실측). 내보내기 파일은 사후
        # 입증에 쓰는 기록이라 화면과 같은 근거가 담겨야 한다.
        if legal_results:
            check.evidence_clause = legal_results[0].citation
            focused_basis = focused_law_paragraphs(legal_results[0].text, hint.focus)
            check.evidence_text = (
                "\n".join(focused_basis) if focused_basis
                else legal_results[0].text[:700]
            )
            # 근거가 여러 조문이면 전부 기록한다. 화면에는 법률 조문과 그 위임을
            # 받은 감독규정이 함께 뜨는데(EXP-001은 3건), 기록에 첫 건만 남으면
            # 그 기록으로는 판정을 설명할 수 없다.
            legal_basis_by_rule[check.rule_id] = [
                {
                    "citation": r.citation,
                    "title": r.title,
                    "origin": r.origin,
                    "applied_text": "\n".join(focused_law_paragraphs(r.text, hint.focus)),
                }
                for r in legal_results
            ]
        if legal_results:
            hint = LAW_HINTS.get(check.rule_id)
            focus = hint.focus if hint else ()
            # 검색 결과는 이미 '이 규칙과 연결되는 조문'만 온다(개수를 채우지 않는다).
            label = "근거 조문" if len(legal_results) == 1 else f"근거 조문 {len(legal_results)}건"
            st.markdown(f"**{label}** (판정과 직접 연결되는 부분을 표시합니다)")
            for rank, result in enumerate(legal_results, start=1):
                tag = "최우선 근거" if rank == 1 else f"근거 {rank}"
                st.markdown(f"- `{tag}` **{result.citation}** · {result.title} · 출처 `{result.origin}`")
                if not result.text:
                    continue
                # 조문 전체를 던지면 '그래서 어디가 문제냐'에 답하지 못한다.
                # 이 규칙이 걸리는 항만 뽑아 문구를 강조해 먼저 보여준다.
                # (예전에는 앞 700자만 잘라 보여줬는데, 금소법 19조의 설명 확인
                #  의무는 ②항이라 그 문장이 화면에 아예 나오지 않았다.)
                focused = focused_law_paragraphs(result.text, focus)
                if focused:
                    st.markdown(
                        '<div class="kb-law">'
                        + "".join(f"<p>{_highlight_law(p, focus)}</p>" for p in focused)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    # 연결되는 조문이 하나도 없을 때만 점수 상위 1건이 여기로 온다.
                    st.caption(
                        "이 조문에서 규칙과 직접 연결되는 문구를 찾지 못했습니다 — "
                        "확인의 출발점으로만 보세요."
                    )
                with st.expander(f"{result.citation} 조문 전체 보기"):
                    st.markdown(
                        '<div class="kb-law kb-law-full">'
                        + "".join(
                            f"<p>{_highlight_law(p, focus)}</p>"
                            for p in split_law_paragraphs(result.text)
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )
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

st.subheader("4. 서류 원문 하이라이트")
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

st.subheader("5. 검증 결과 내보내기")
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
        "elderly_investor": package["elderly"],
        "non_face_to_face": package["nonface"],
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
            "evidence_text": check.evidence_text,
            "legal_basis": legal_basis_by_rule.get(check.rule_id, []),
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
