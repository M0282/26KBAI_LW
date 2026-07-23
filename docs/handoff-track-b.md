# 합류 가이드 — 현재 상태 & Track B(파서·UI) 할 일

M0282 합류 시점 기준. tommy가 Track A 코어와 파서 골격까지 먼저 진행했다.
이 문서로 "무엇이 끝났고 / 무엇을 이식하면 되는지 / 무엇을 다시 만들지 말지"를 한눈에.

## 1. 지금 파이프라인이 관통한다 (실측 완료)

```
PDF(parser) → ParsedDocument → 조문검색(BM25) + LLM판정 + 교차검증(verify) → VerificationReport
```

실제 PDF 2종(적합성 진단표 '안정형' + 상품설명서 '1등급')으로 돌려서
금소법 17조(적합성원칙) 위반을 근거 조문과 함께 적발하는 것까지 확인됨.
- 무료 오프라인 모드로도 교차검증 동작 / 실제 LLM 판정도 검증 완료
- 데모 러너: `py -3 -m scripts.demo_verify [--llm] <pdf...>` (e2e-demo 브랜치)

## 2. 완성됨 — 다시 만들지 말 것 (tommy/Track A 소유)

| 모듈 | 내용 | 위치 |
|---|---|---|
| `src/ingest/law_api.py` | 국가법령정보 API 클라이언트 | main |
| `src/ingest/articles.py` | 본문 → 조문 청킹 | main |
| `src/ingest/fetch_regulations.py` | 규정 일괄 수집 (464조문 실측) | main |
| `src/ingest/index.py` | BM25 조문 검색 | **PR #7 머지 시 반영** |
| `src/verify/engine.py` | 교차검증 + LLM 의무판단 + 환각방지 | **PR #7** |
| `src/verify/llm.py` | 공급자 교체 가능 LLM (Claude/Offline) + 프롬프트 캐싱 | **PR #7** |

## 3. Track B 할 일 (M0282 소유 — kb_rulelens에서 이식)

kb_rulelens에 이미 좋은 코드가 있으니 **새로 짜지 말고 이식·연결**한다.

| 할 일 | kb_rulelens 원본 | 우리 쪽 연결 지점 | 비고 |
|---|---|---|---|
| ① 문서 로더 확장 | `document_loader.py` (DOCX/TXT) | `src/parser/pdf_loader.py` (PR #5, PyMuPDF·좌표) | PDF는 이미 됨. DOCX 로더만 추가 |
| ② **필드 추출 분석기** | `ai_analyzer.py` (분류+필드추출) | `src/parser/`에 신규 | **현재 빠진 핵심 조각.** raw_text → doc_type + fields(투자성향/위험등급 등) → ParsedDocument |
| ③ Streamlit UI | `app.py` | `app/main.py` (골격만 있음) | 업로드 → VerifyEngine.verify → 리포트 시각화 |
| ④ 리포트 시각화 | app.py 탭/매트릭스 | VerificationReport 렌더 | 근거 조문 + **좌표 하이라이트**(pdf_loader.locate) 연동 |

**②가 가장 급함**: 지금은 데모 러너가 임시 규칙(regex)으로 투자성향/위험등급만 뽑는다.
kb_rulelens의 ai_analyzer(LLM 분류+추출)를 이식해 ParsedDocument.fields를 제대로 채우면
검증 엔진이 실제 업로드 서류에서 자율 동작한다. (LLM은 Claude로 통일 — `src/verify/llm.py` 참고)

## 4. 인터페이스 계약 (건드릴 때 상의)

`src/common/schemas.py`가 유일한 진실. Track B가 만드는 것 → `ParsedDocument`(doc_type, fields, raw_text).
Track A가 돌려주는 것 → `VerificationReport`(checks[근거조문/근거문장/제안], summary).
- 교차검증(적합성진단표↔상품설명서)을 위해 fields에 최소 **'투자성향', '위험등급'** 을 담아야 함.
- 스키마 변경은 팀 상의 후 단독 PR.

## 5. 열린 PR 머지 순서 (권장)

1. **#5** (PyMuPDF 파서) → main
2. **#7** (index.py + 검증엔진 + 캐싱) → main — index.py 포함이라 **꼭 머지**
   - 주의: #5·#7 둘 다 requirements.txt를 건드림 → 나중 머지 쪽에서 사소한 충돌 시 양쪽 라인 모두 유지
3. 이후 e2e-demo의 데모 러너를 main 기준으로 재정리해 추가

## 6. 팀 규칙 리마인드
- LLM API 비용 절감: 프롬프트 캐싱(적용됨, 입력 87%↓ 실측) / 개발 중 OfflineClient / 발표는 사전 캐싱
- 협업: main 직접 푸시 금지, `feat/<이름>/<작업>` + PR, 상대 모듈은 Issue로 요청 (AGENTS.md)
- 분담표(docs/PLAN.md 2장)의 "(미정)"을 실제 담당자로 채우기: Track A=tommy, Track B=m0282
