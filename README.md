# 26KBAI_LW — 컴플라이언스 서류 자동 검증 AI Copilot

KB국민은행 제8회 AI Challenge 출품작.
금융상품 판매서류(적합성 진단표·상품설명서·가입신청서·설명확인서)를 자동 판독하고,
**서류 사이의 불일치**를 금융소비자보호법 기준으로 검증해 불완전판매 위험을 판매 시점에 적출합니다.
판정 근거는 서류 원문 위치와 법령 조문으로 함께 제시합니다.

> 역대 본선작이 '문서 하나 안'을 보거나 '규정 ↔ 규정'을 대조한다면,
> 우리는 **'서류 ↔ 서류 ↔ 법령'** 교차 검증입니다.
> 적합성 위반은 고객 정보(진단표)와 상품 정보(설명서)가 다른 문서에 있어
> 한 장만 봐서는 정의조차 할 수 없습니다.

**설계 원칙 — LLM은 읽고, 판정은 코드가 한다.**
같은 서류·같은 정책이면 언제나 같은 결과가 나옵니다(실측: 업로드 순서 24가지 → 판정 조합 1가지).

## 문서

- [협업 계획 / 역할 분담 / 16일 일정](docs/PLAN.md)
- [LLM 협업 규칙](AGENTS.md) — Claude Code·ChatGPT Codex 등 LLM 도구가 자동으로 따르는 규칙
  (Claude Code용 `CLAUDE.md`는 `AGENTS.md`를 가리키는 포인터 — 규칙 수정은 AGENTS.md에서만)

## 빠른 시작

**요구 사항: Python 3.10 이상** (개발·검증은 3.12 기준)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env         # ANTHROPIC_API_KEY 채우기 (.env는 커밋 금지)
streamlit run app/main.py
```

비정형 서류에서 판정에 필요한 값을 읽으려면 `ANTHROPIC_API_KEY` 가 필요합니다.
키가 없으면 문서유형·위험등급을 신뢰할 수 있게 추출하지 못해 검증을 중단합니다
— 잘못된 판정을 내놓지 않기 위한 의도된 동작입니다.

스캔본·사진은 Tesseract OCR로 읽고, OCR이 실패하면 LLM 비전 판독으로 자동 전환합니다.
한국어 OCR에는 Tesseract와 `kor.traineddata` 가 필요하며, 없으면 비전 판독으로 처리됩니다.

### 바로 해보기

`data/samples/demo/` 의 서류 4종을 한 칸에 올리면 의도적으로 심어둔 위반 3건이 적발됩니다
— 적합성 위반(FIT-001), 설명일·계약일 역전(DATE-001), 미서명(ACK-001).

## 구조

```
src/ingest/    법령 수집 + BM25 조문 검색
src/verify/    검증 규칙 8종 (결정론적 판정) + 법적 쟁점 생성
src/parser/    서류 판독(텍스트→OCR→비전) + 필드 추출 + 하이라이트 좌표
src/common/    공용 스키마 = 모듈 간 인터페이스 계약 (변경 시 상의)
app/main.py    Streamlit 검증 화면
data/          샘플 서류·법령 조문 (민감/대용량 데이터 커밋 금지)
scripts/       데모 생성·검증 하니스·감사·기술설명서 생성
tests/         회귀 테스트 45건
docs/          계획 문서 · 기술설명서(PPTX)
```

## 협업 핵심 규칙 (상세: AGENTS.md)

- main 직접 푸시 금지 — 모든 변경은 `feat/<이름>/<작업>` 브랜치 + PR
- 작업 시작 전 `git pull origin main` + 열린 Issue/PR 확인
- 모듈 소유권 준수: 상대 모듈은 Issue로 요청, 직접 수정 금지
- `.env`·API 키 커밋 절대 금지
