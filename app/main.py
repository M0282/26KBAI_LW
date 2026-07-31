"""KB 금융상품 판매서류 검증 AI Copilot MVP."""
from __future__ import annotations

import hashlib
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
from src.common.schemas import CheckStatus, EvidenceRef, ParsedDocument, RuleCheck
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
REPORT_SCHEMA_VERSION = "1.2.0"
RULE_SET_VERSION = "2026-07-31"
STATUS_LABEL = {
    CheckStatus.PASS: ("통과", "#2E7D32"),
    CheckStatus.WARNING: ("주의", "#B26A00"),
    CheckStatus.MISSING: ("누락", "#C62828"),
    CheckStatus.RISK: ("위험", "#C62828"),
}

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _package_fingerprint(
    raw_map: dict[str, bytes], *, elderly: bool, nonface: bool,
    with_llm: bool, live_law: bool,
) -> str:
    """파일명 대신 실제 파일 내용과 판정 설정으로 판매 건을 식별한다."""
    digest = hashlib.sha256()
    for name in sorted(raw_map):
        digest.update(name.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(raw_map[name])
        digest.update(b"\0")
    digest.update(
        f"elderly={int(elderly)}|nonface={int(nonface)}|"
        f"llm={int(with_llm)}|live_law={int(live_law)}".encode("ascii")
    )
    return digest.hexdigest()


def _regulation_corpus_hash() -> str | None:
    """현재 로컬 법령 조문 묶음의 내용 해시. 재현성 확인용이다."""
    paths = sorted((ROOT / "data" / "regulations").glob("*.articles.json"))
    if not paths:
        return None
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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


_EVIDENCE_TYPE_LABEL = {
    "field": "추출 필드",
    "text": "원문 문구",
    "missing_field": "필수 항목 미확인",
    "missing_document": "필수 문서 누락",
    "manual_review": "수동 확인 필요",
}


def _evidence_hits(evidence: EvidenceRef, pdf_details: dict) -> list[dict]:
    """구조화 근거를 PDF 좌표와 연결한다.

    원문 문장 → 정규화 값 → 짧은 발췌 순으로 시도한다. LLM 비전 판독값처럼
    텍스트 레이어에 없는 근거는 빈 목록을 반환하고 화면에서 그 사실을 밝힌다.
    """
    if not evidence.document_id or evidence.document_id not in pdf_details:
        return []
    pdf = pdf_details[evidence.document_id]
    candidates = [evidence.search_text, evidence.value, evidence.excerpt]
    tried: set[str] = set()
    for candidate in candidates:
        query = (candidate or "").strip()
        if not query or query in tried or query.startswith("AI 비전 판독:"):
            continue
        tried.add(query)
        hits = pdf.locate(query)
        if hits:
            return hits
    return []


def _evidence_payload(evidence: EvidenceRef, pdf_details: dict) -> dict:
    payload = evidence.model_dump()
    payload["locations"] = _evidence_hits(evidence, pdf_details)
    return payload


def _render_rule_evidence(
    check: RuleCheck,
    pdf_details: dict,
    pdf_bytes_map: dict[str, bytes],
    *,
    active_slot: int,
) -> None:
    """규칙별 구조화 근거와 해당 PDF 위치를 같은 자리에서 보여준다."""
    if not check.evidence_items:
        if check.document_excerpt:
            st.markdown(
                f'<div class="kb-evidence"><b>서류 근거 요약</b><br>'
                f'{html.escape(check.document_excerpt)}</div>',
                unsafe_allow_html=True,
            )
        return

    st.markdown("**판정 근거**")
    for index, evidence in enumerate(check.evidence_items, start=1):
        kind = _EVIDENCE_TYPE_LABEL.get(evidence.evidence_type, evidence.evidence_type)
        document = evidence.document_id or "판매건 전체"
        page = f" · {evidence.page}쪽" if evidence.page else ""
        field = f" · `{evidence.field_name}`" if evidence.field_name else ""
        value = evidence.value or evidence.excerpt or "미확인"
        st.markdown(
            f'<div class="kb-evidence"><b>{index}. {html.escape(kind)}</b> · '
            f'{html.escape(document)}{html.escape(page)}{field}<br>'
            f'{html.escape(value)}</div>',
            unsafe_allow_html=True,
        )

        if evidence.evidence_type not in {"field", "text"} or not evidence.document_id:
            continue
        hits = _evidence_hits(evidence, pdf_details)
        if not hits:
            st.caption(
                "이 근거는 추출값으로 기록됐지만 PDF 텍스트 좌표는 찾지 못했습니다. "
                "스캔·비전 판독값이거나 OCR 공백 차이일 수 있으니 원문을 확인하세요."
            )
            continue

        toggle_key = f"evidence::{active_slot}::{check.rule_id}::{index}"
        if st.toggle("원문 근거 보기", key=toggle_key):
            preferred = next(
                (hit for hit in hits if evidence.page and hit["page"] == evidence.page),
                hits[0],
            )
            image = render_highlighted_page(
                pdf_bytes_map[evidence.document_id],
                page_number=preferred["page"],
                rects=preferred["rects"],
            )
            st.image(
                image,
                caption=(
                    f"{evidence.document_id} · {preferred['page']}페이지 · "
                    f"{evidence.field_name or '원문 문구'} 근거 위치"
                ),
                use_container_width=True,
            )
            st.caption(
                "동일 문구가 여러 페이지에 있으면 JSON의 document_evidence.items.locations에 "
                "모든 좌표가 함께 기록됩니다."
            )


def _render_action_plan(check: RuleCheck) -> None:
    action = check.action_plan
    if not action:
        st.success("추가 조치 없음")
        return
    blocking = "예 — 조치 완료 전 판매 중단" if action.sale_blocking else "아니오 — 확인 후 진행 가능"
    st.markdown("**필요한 조치**")
    st.markdown(
        f'<div class="kb-evidence"><b>담당:</b> {html.escape(action.responsible_role)}<br>'
        f'<b>조치:</b> {html.escape(action.required_action)}<br>'
        f'<b>판매 차단:</b> {html.escape(blocking)}<br>'
        f'<b>완료 기준:</b> {html.escape(action.completion_criteria)}</div>',
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="KB 금융상품 판매서류 검증 AI Copilot", page_icon="🛡️", layout="wide")
st.markdown(
    f"""
<style>
:root {{ --kb-yellow:{KB_YELLOW}; --kb-yellow2:{KB_YELLOW_ALT}; --kb-gray:{KB_GRAY}; }}
/* 기존 System / Light / Dark 테마 전환을 유지한다.
   앱 배경을 밝게 강제하지 않아 현재 테마의 배경과 기본 글자색을 그대로 따른다. */
html, body, .stApp, button, input, textarea, select {{
  font-family:"Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",Arial,sans-serif;
}}
/* Streamlit 상단 고정 헤더와 첫 콘텐츠가 겹치지 않도록 여백을 확보한다. */
.block-container {{
  padding-top:3.75rem;
  max-width:1500px;
}}
.kb-hero {{
  background:white;
  color:#3a3630;
  border:1px solid #eee8da;
  border-radius:22px;
  padding:28px 28px 24px;
  box-shadow:0 10px 30px rgba(100,91,76,.08);
  margin:0 0 18px;
  overflow:visible;
}}
.kb-title {{
  color:{KB_GRAY};
  font-size:2.1rem;
  font-weight:800;
  line-height:1.28;
  letter-spacing:-0.02em;
  margin:0;
  padding-top:2px;
}}
.kb-title b {{ color:{KB_YELLOW}; }}
.kb-sub {{ color:#655f55; margin-top:8px; font-size:1.02rem; }}
.kb-badge {{ display:inline-block; border:1px solid {KB_YELLOW}; background:#fff8df; color:{KB_GRAY};
padding:7px 12px; border-radius:999px; margin-top:13px; font-weight:700; }}
.kb-card {{ background:white; color:#3a3630; border:1px solid #eee8da; border-radius:18px; padding:18px;
box-shadow:0 8px 24px rgba(100,91,76,.07); min-height:145px; }}
/* 첫 화면의 3단계 안내 박스만 동일한 크기와 KB Yellow 왼쪽 강조선을 적용한다.
   다른 kb-card와 테마·다크모드 관련 스타일은 변경하지 않는다.

   높이를 px로 고정하면 안 된다 — 창 폭·확대 배율·글꼴이 조금만 달라도 글자가
   상자 밖으로 흘러넘친다(실측: 1500px/100%에서도 10px, 1280px/125%에서 67px).
   그리드로 세 칸의 높이를 맞추고, 내용이 길어지면 세 칸이 함께 커지게 한다.
   제목 크기도 명시한다 — Streamlit 기본 h3는 28px이라 카드 폭에서 줄이 넘친다. */
.kb-intro-grid {{
  display:grid;
  grid-template-columns:repeat(3, minmax(0, 1fr));
  gap:16px;
  align-items:stretch;
  margin-top:4px;
}}
.kb-intro-card {{
  width:100%;
  min-height:175px;
  box-sizing:border-box;
  border-left:5px solid {KB_YELLOW};
  display:flex;
  flex-direction:column;
  /* 한글은 어절 단위로 끊는다 — 없으면 '업로 / 드'처럼 낱말 한가운데가 잘린다.
     overflow-wrap 은 함께 쓰지 않는다. anywhere/break-word 를 주면 좁은 칸에서
     keep-all 을 무시하고 다시 낱말을 쪼갠다(실측: '판매 건별 업 / 로드'). */
  word-break:keep-all;
}}
/* Streamlit 이 제목·본문에 word-break:break-word 를 '직접' 걸어 두어 상위의
   keep-all 이 상속되지 않는다(실측). 그래서 여기서 다시 지정한다.
   overflow-wrap 도 normal 로 되돌려야 낱말을 쪼개지 않는다. */
.kb-intro-card h3 {{ margin:0 0 14px; font-size:1.2rem; line-height:1.4; font-weight:700;
  word-break:keep-all; overflow-wrap:normal; }}
.kb-intro-card p {{ margin:0; font-size:0.9rem; line-height:1.6;
  word-break:keep-all; overflow-wrap:normal; }}
@media (max-width: 900px) {{
  .kb-intro-grid {{ grid-template-columns:1fr; }}
  .kb-intro-card {{ min-height:0; }}
}}
.kb-evidence {{ background:#fff8df; color:#3a3630; border-left:4px solid {KB_YELLOW}; padding:10px 12px; border-radius:8px; }}
.kb-law {{ background:#fcfbf8; border:1px solid #eee8da; border-left:4px solid {KB_GRAY};
padding:10px 14px; border-radius:8px; margin:6px 0 10px; }}
.kb-law p {{ margin:0 0 8px; font-size:0.9rem; line-height:1.62; color:#3a3630; }}
.kb-law p:last-child {{ margin-bottom:0; }}
.kb-law mark {{ background:{KB_YELLOW}; color:#241f18; padding:1px 2px; border-radius:3px; font-weight:700; }}
.kb-law-full p {{ font-size:0.83rem; color:#5a544b; }}
/* 업로더는 배경을 칠하지 않는다. 안쪽 글자는 Streamlit이 테마 색으로 그리므로
   여기서 흰 배경을 깔면 다크모드에서 흰 글자가 흰 바탕에 얹혀 라벨이 사라진다. */
div[data-testid="stFileUploader"] {{ padding:12px; border-radius:16px; border:1px dashed {KB_YELLOW}; }}
.stButton button {{ background:{KB_YELLOW}; color:#332c22; border:none; font-weight:800; border-radius:10px; }}

@media (max-width: 768px) {{
  .block-container {{ padding-top:3.25rem; }}
  .kb-hero {{ padding:22px 20px 20px; border-radius:18px; }}
  .kb-title {{ font-size:1.72rem; line-height:1.32; }}
}}
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
    # 세 칸을 st.columns 로 나누면 칸마다 별도 블록이라 높이가 서로 맞지 않는다.
    # 한 덩어리 그리드로 그려야 세 칸이 항상 같은 높이가 되고, 어느 컴퓨터에서든
    # 같은 모양이 나온다(칸 높이를 px로 고정할 필요가 없어진다).
    st.markdown(
        '<div class="kb-intro-grid">'
        '<div class="kb-card kb-intro-card"><h3>① 판매 건별 업로드</h3>'
        '<p>상품 계약 하나에 필요한 서류 4종을 한 칸에 올립니다.</p></div>'
        '<div class="kb-card kb-intro-card"><h3>② AI 문서 이해</h3>'
        '<p>문서 분류, 필드 추출, 표현 정규화와 법적 검색 쟁점을 생성합니다.</p></div>'
        '<div class="kb-card kb-intro-card"><h3>③ 근거 기반 판정</h3>'
        '<p>결정론적 규칙으로 판정하고 서류 원문과 관련 조문을 함께 보여줍니다.</p></div>'
        '</div>',
        unsafe_allow_html=True,
    )
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


def FIELD_SOURCE_TYPE(field) -> str:
    """JSON 재사용을 위한 고정 코드. 화면용 한글 라벨과 분리한다."""
    if not field.value:
        return "unavailable"
    confidence = field.confidence or 0.0
    if confidence >= 0.9:
        return "deterministic_scan"
    if confidence >= 0.8:
        return "ai_vision"
    if confidence >= 0.5:
        return "grounded_match"
    return "ai_extraction"


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
        "uploaded_count": len(uploaded_packages[slot]),
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

action_rows = []
for check in checks:
    action = check.action_plan
    if not action:
        continue
    action_rows.append({
        "우선순위": "즉시" if action.sale_blocking else "확인",
        "규칙": check.rule_id,
        "상태": STATUS_LABEL[check.status][0],
        "담당자": action.responsible_role,
        "필요한 조치": action.required_action,
        "판매 차단": "예" if action.sale_blocking else "아니오",
    })
if action_rows:
    st.markdown("#### 조치 필요 항목 요약")
    action_rows.sort(key=lambda row: (row["판매 차단"] != "예", row["규칙"]))
    st.dataframe(action_rows, hide_index=True, use_container_width=True)
else:
    st.success("현재 검사 범위에서 추가 조치가 필요한 항목이 없습니다.")

# 규칙별 근거 조문을 모아 둔다. 화면과 내보내기가 같은 근거를 담아야 한다.
legal_basis_by_rule: dict[str, list[dict]] = {}

for check in checks:
    label, color = STATUS_LABEL[check.status]
    issue = issues[check.rule_id]
    with st.expander(f"[{label}] {check.rule_id} · {check.description}", expanded=check.status != CheckStatus.PASS):
        st.markdown(f"**판정:** <span style='color:{color};font-weight:800'>{label}</span>", unsafe_allow_html=True)
        _render_rule_evidence(
            check, pdf_details, pdf_bytes_map, active_slot=active_slot
        )
        # 설명이 LLM이 쓴 것인지 미리 정해둔 폴백 문구인지 밝힌다.
        # 공식 조치는 LLM이 아니라 결정론적 규칙이 별도로 제시한다.
        source_label = "AI 쟁점 설명" if issue.used_llm else "규칙 기반 설명(LLM 미사용)"
        st.markdown(f"**{source_label}:** {html.escape(issue.rationale)}")
        _render_action_plan(check)

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
package_key = _package_fingerprint(
    pdf_bytes_map,
    elderly=package["elderly"],
    nonface=package["nonface"],
    with_llm=use_llm,
    live_law=live_law,
)
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
all_legal_basis = [
    item
    for items in legal_basis_by_rule.values()
    for item in items
]

# 실제 이번 검증에서 조회·판정 근거로 연결된 조문을 실행 결과에서 동적으로 만든다.
# dict.fromkeys를 쓰면 규칙 실행 순서는 유지하면서 중복 조문만 제거할 수 있다.
applied_legal_articles = list(dict.fromkeys(
    item.get("citation")
    for item in all_legal_basis
    if item.get("citation")
))

# 통과가 아닌 항목에 연결된 조문은 사람이 다시 확인해야 할 법적 근거로 별도 기록한다.
review_legal_articles = list(dict.fromkeys(
    item.get("citation")
    for check in checks
    if check.status != CheckStatus.PASS
    for item in legal_basis_by_rule.get(check.rule_id, [])
    if item.get("citation")
))

legal_origins = sorted({item.get("origin", "") for item in all_legal_basis if item.get("origin")})
live_law_succeeded = "law.go.kr" in legal_origins

# Streamlit 재실행 때마다 바뀌지 않도록, 같은 파일·설정 조합의 최초 검증 시각을 보존한다.
verified_at = st.session_state.setdefault(
    f"verified_at::{package_key}",
    datetime.now().astimezone().isoformat(timespec="seconds"),
)
exported_at = datetime.now().astimezone().isoformat(timespec="seconds")
reasoning_model = (
    (os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5")
    if any(issue.used_llm for issue in issues.values())
    else None
)
vision_model = (
    (os.environ.get("VISION_MODEL") or "claude-haiku-4-5")
    if any(pdf.vision_applied for pdf in pdf_details.values())
    else None
)
extraction_models = sorted({
    meta.model_used
    for meta in extraction_meta.values()
    if getattr(meta, "model_used", None)
})

report = {
    "schema_version": REPORT_SCHEMA_VERSION,
    "verified_at": verified_at,
    "exported_at": exported_at,
    "tool": "KB 금융상품 판매서류 검증 AI Copilot (MVP)",
    "package": {
        "package_id": package_key[:16],
        "label": package["label"],
        "slot": package["slot"] + 1,
        "package_count_in_session": len(packages),
        "export_scope": "selected_package_only",
    },
    "scope": {
        "description": "금융상품 판매서류 교차 검증 MVP",
        "rule_count": 8,
        "rule_ids": [
            "PKG-001", "FIT-001", "EXP-001", "DATE-001",
            "ACK-001", "ADV-001", "DOC-001", "REC-001",
        ],
        # 이 목록은 현재 규칙 엔진이 지원하는 전체 법률 범위다.
        # 실제 이번 판매 건에서 연결된 조문은 아래 legal_articles 섹터에 따로 기록한다.
        "legal_articles": [
            "금융소비자 보호에 관한 법률 제17조",
            "금융소비자 보호에 관한 법률 제19조",
            "금융소비자 보호에 관한 법률 제21조",
            "금융소비자 보호에 관한 법률 제23조",
            "금융소비자 보호에 관한 법률 제28조",
        ],
        "legal_articles_meaning": "supported_scope",
        "excluded_scope": [
            "제18조 적정성원칙",
            "제20조 불공정영업행위",
            "제22조 광고 관련 의무",
        ],
    },
    "legal_articles": {
        # 실제 법령 검색 결과에 존재하는 조문만 들어가므로 하드코딩 목록이 아니다.
        "applied": applied_legal_articles,
        # 위험·누락·주의 판정에 연결되어 담당자 재검토가 필요한 조문만 별도 표시한다.
        "requiring_review": review_legal_articles,
        # 어떤 규칙이 어떤 조문을 사용했는지 추적할 수 있도록 규칙별 연결도 남긴다.
        "by_rule": [
            {
                "rule_id": check.rule_id,
                "status": check.status.value,
                "citations": [
                    item.get("citation")
                    for item in legal_basis_by_rule.get(check.rule_id, [])
                    if item.get("citation")
                ],
            }
            for check in checks
        ],
    },
    "settings": {
        "llm_requested": use_llm,
        "live_law_lookup_requested": live_law,
        "profile_min_grade": dict(DEFAULT_PROFILE_MIN_ALLOWED_GRADE),
        "elderly_investor": package["elderly"],
        "non_face_to_face": package["nonface"],
    },
    "execution": {
        "llm_actually_used": any(meta.used_llm for meta in extraction_meta.values())
        or any(issue.used_llm for issue in issues.values()),
        "fallback_occurred": any(
            getattr(meta, "fallback_occurred", False) for meta in extraction_meta.values()
        ),
        "extraction_models_used": extraction_models,
        "reasoning_model_used": reasoning_model,
        "vision_model_used": vision_model,
        "law_lookup": {
            "live_lookup_requested": live_law,
            "live_lookup_succeeded": live_law_succeeded,
            "fallback_to_local": bool(live_law and "local" in legal_origins),
            "sources_used": legal_origins,
        },
    },
    "versions": {
        "report_schema": REPORT_SCHEMA_VERSION,
        "rule_set": RULE_SET_VERSION,
        "git_commit": os.environ.get("GITHUB_SHA") or os.environ.get("GIT_COMMIT"),
        "law_corpus_sha256": _regulation_corpus_hash(),
    },
    "processing": {
        "status": "partial" if errors else "complete",
        "uploaded_document_count": package.get(
            "uploaded_count", len(parsed_documents) + len(errors)
        ),
        "processed_document_count": len(parsed_documents),
        "failed_document_count": len(errors),
        "failures": errors,
    },
    "documents": [
        {
            "document_id": document.document_id,
            "sha256": _sha256_bytes(pdf_bytes_map[document.document_id]),
            "file_size_bytes": len(pdf_bytes_map[document.document_id]),
            "doc_type": document.doc_type,
            "read_mode": (
                "ai_vision" if pdf_details[document.document_id].vision_applied
                else "ocr" if pdf_details[document.document_id].ocr_applied
                else "text_layer"
            ),
            "extraction": {
                "used_llm": extraction_meta[document.document_id].used_llm,
                "model_used": getattr(
                    extraction_meta[document.document_id], "model_used", None
                ),
                "models_attempted": list(
                    getattr(
                        extraction_meta[document.document_id],
                        "models_attempted",
                        (),
                    )
                ),
                "fallback_occurred": getattr(
                    extraction_meta[document.document_id],
                    "fallback_occurred",
                    False,
                ),
                "warning": extraction_meta[document.document_id].warning,
            },
            "fields": [
                {
                    "name": field.name,
                    "value": field.value,
                    "page": field.page,
                    "confidence": field.confidence,
                    "evidence_text": field.evidence_text,
                    "source_type": FIELD_SOURCE_TYPE(field),
                    "source_label": FIELD_SOURCE_LABEL(field),
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
            "document_evidence": {
                "summary": check.document_excerpt,
                # 각 근거는 문서·필드·페이지·원문과 PDF 좌표까지 함께 기록한다.
                "items": [
                    _evidence_payload(item, pdf_details)
                    for item in check.evidence_items
                ],
                # 1.1 소비자와의 호환을 위해 기존 키도 유지한다.
                "excerpt": check.document_excerpt,
            },
            "legal_basis": legal_basis_by_rule.get(check.rule_id, []),
            "explanation": {
                "rationale": issues[check.rule_id].rationale,
                "generated_by_llm": issues[check.rule_id].used_llm,
                "search_query": issues[check.rule_id].search_query,
                # 하위 호환 필드지만 값은 규칙 엔진의 공식 조치로 고정한다.
                "recommended_action": (
                    check.action_plan.required_action
                    if check.action_plan else "추가 조치 없음"
                ),
                "recommended_action_generated_by_llm": False,
            },
            "action_plan": (
                check.action_plan.model_dump() if check.action_plan else None
            ),
            "rule_suggestion": check.suggestion,
            # 기존 소비자와의 호환을 위해 대표 근거 필드는 유지한다.
            "evidence_clause": check.evidence_clause,
            "evidence_text": check.evidence_text,
        }
        for check in checks
    ],
    "metrics": {
        "document_count": metrics.document_count,
        "check_count": metrics.check_count,
        "blocker_count": metrics.blocker_count,
        "warning_count": metrics.warning_count,
        "elapsed_seconds": metrics.elapsed_seconds,
    },
    # 기존 스키마 호환 필드. 새 소비자는 processing.failures를 사용한다.
    "unread_documents": errors,
}
st.download_button(
    f"{package['label']} 검증 결과 JSON 내려받기",
    data=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
    file_name=(
        f"검증결과_판매건{package['slot'] + 1}_"
        f"{datetime.now():%Y%m%d_%H%M%S}.json"
    ),
    mime="application/json",
)

st.caption("주의: 이 MVP는 법률 위반을 확정하지 않으며, 규정 준수 여부의 추가 검토가 필요한 지점을 선별합니다.")
