"""Streamlit 데모 엔트리 (Track B).

실행: streamlit run app/main.py
"""
import streamlit as st

st.set_page_config(page_title="컴플라이언스 서류 검증 Copilot", page_icon="📋", layout="wide")

st.title("📋 컴플라이언스 서류 자동 검증 AI Copilot")
st.caption("제출 서류를 규정과 대조해 누락·위험 조항을 적출하고 근거 조문을 제시합니다.")

uploaded = st.file_uploader(
    "검증할 서류 업로드 (PDF)", type=["pdf"], accept_multiple_files=True
)

if uploaded:
    st.info(
        f"{len(uploaded)}건 업로드됨 — 검증 파이프라인 연결 예정 "
        "(src/parser → src/verify → VerificationReport 렌더링)"
    )
else:
    st.write("← 좌측 없이 이 화면에서 바로 서류를 올리면 검증이 시작됩니다. (파이프라인 연결 전)")
