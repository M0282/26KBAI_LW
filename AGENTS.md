# LLM 협업 규칙 (Claude Code, OpenAI Codex 등 모든 AI 세션은 이 규칙을 반드시 따를 것)

이 레포는 2인 팀 + 각자의 LLM 도구(Claude Code / ChatGPT Codex)가 함께 작업한다.
이 파일이 규칙의 원본이다 — CLAUDE.md는 이 파일을 가리키는 포인터일 뿐, 규칙 수정은 여기서만 한다.
충돌 방지의 핵심: **main 직접 푸시 금지, 모듈 소유권 준수, 작게 자주 PR.**

## 세션 시작 시 반드시
1. `git pull origin main` 으로 최신화
2. 열린 Issue / PR 목록 확인 — 상대가 진행 중인 작업과 겹치지 않는지 확인
3. 새 작업이면 Issue 먼저 만들고(또는 할당받고) 브랜치 생성

## 브랜치 / PR 규칙
- **main 에 직접 푸시 금지** (오탈자 수정도 PR로)
- 브랜치 이름: `feat/<이름>/<작업>` — 예: `feat/tommy/doc-parser`, `feat/m0282/law-rag`
- 1 Issue = 1 브랜치 = 1 PR. PR은 하루 단위 이하로 작게 자주
- force-push 금지, 상대방 브랜치에 푸시 금지
- 머지는 사람이 GitHub에서 승인 후 진행 (LLM이 스스로 머지하지 않는다)

## 모듈 소유권 (충돌 방지의 핵심)
- 소유권 분담은 `docs/PLAN.md`의 분담표를 따른다
- 상대 소유 모듈을 고쳐야 하면: 직접 수정하지 말고 Issue로 요청
- 공용 파일(`src/common/`, `docs/`, 설정 파일) 변경은 **단독 PR**로 분리하고 PR 설명에 이유 명시

## 인터페이스 계약
- 모듈 간 주고받는 데이터 구조는 전부 `src/common/schemas.py` 에 정의되어 있다
- 스키마를 바꾸면 상대 모듈이 깨진다 — **스키마 변경은 반드시 팀 상의 후 별도 PR**

## 커밋
- 메시지 형식: `<type>: <요약>` — type은 feat / fix / docs / chore / refactor / test
- `.env`, API 키, 개인정보 포함 데이터는 절대 커밋 금지 (`.gitignore` 확인)
- `data/` 아래 대용량 원본 데이터 커밋 금지 (샘플 소량만 허용)

## 프로젝트 컨텍스트
- 과제: 컴플라이언스 서류 자동 검증 AI Copilot (KB 8회 AI Challenge, 마감 ~2026-08-03)
- 포지셔닝: 7회 입상작(규정↔규정 모니터링)과 달리 **제출 서류↔규정 검증 + 근거 조문 하이라이트**
- 상세 계획·일정·분담: `docs/PLAN.md`
