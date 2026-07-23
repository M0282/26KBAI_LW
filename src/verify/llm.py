"""검증 엔진용 LLM 클라이언트 (공급자 교체 가능).

- ClaudeClient: Anthropic Claude (기본). 구조화 출력(JSON schema)으로 판정 결과를 받는다.
- OfflineClient: API 키 없이 규칙 기반으로 근사 판정 (발표장 네트워크 장애 대비 + 개발 테스트용).

앱이 호출하는 LLM 공급자는 팀 합류 시 하나로 통일한다(Claude vs OpenAI 미정).
그때 이 파일의 클라이언트만 교체하면 되도록 인터페이스(complete_json)를 좁게 유지.
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol

MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-5")  # 비용/품질 균형 (팀 결정)


class LLMClient(Protocol):
    def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        """system+prompt를 주고 schema에 맞는 JSON 하나를 받는다."""
        ...

    @property
    def mode(self) -> str: ...


class ClaudeClient:
    def __init__(self, api_key: str | None = None, model: str = MODEL):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model

    @property
    def mode(self) -> str:
        return f"claude:{self.model}"

    def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        # 프롬프트 캐싱: system(고정 지시+참고 조문)을 캐시 프리픽스로 둔다.
        # 같은 조문을 매 서류마다 재사용 → 2회차부터 입력 토큰 ~90% 절감.
        # 가변 부분(서류 원문)은 messages(뒤쪽)에 두어 프리픽스를 깨지 않게 한다.
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("LLM이 요청을 거부했습니다(refusal).")
        self.last_usage = resp.usage  # 캐시 적중 확인용
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text)


class OfflineClient:
    """LLM 없이 동작하는 폴백. 규정 후보는 BM25가 이미 제공하므로,
    여기서는 '적용 대상 의무 판단'을 보수적으로 근사한다(누락 후보를 넓게 잡음).
    실제 판정 품질은 낮지만 파이프라인 전체를 키 없이 돌려볼 수 있다."""

    @property
    def mode(self) -> str:
        return "offline"

    def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        # 오프라인에서는 LLM 판정을 생략하고 빈 결과를 돌려준다.
        # (형식·교차 검증 등 규칙 기반 체크는 엔진에서 별도로 수행됨)
        return {"checks": []}


def get_client(force_offline: bool = False) -> LLMClient:
    """환경에 맞는 클라이언트 선택. 키 없거나 SDK 없으면 오프라인으로 폴백."""
    if force_offline:
        return OfflineClient()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return OfflineClient()
    try:
        return ClaudeClient()
    except Exception:
        return OfflineClient()
