"""검증 결과를 법령 검색 쟁점으로 변환하는 제한형 LLM 모듈.

LLM은 결정론적 RuleCheck의 상태를 변경하지 않는다. 검색 질의·설명·권고 문구만 생성하며,
API 키가 없거나 응답이 잘못되면 사전 정의된 법령 힌트로 폴백한다.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from src.common.schemas import CheckStatus, ParsedDocument, RuleCheck
from src.verify.financial_rules import LAW_HINTS


@dataclass(frozen=True)
class LegalIssue:
    rule_id: str
    search_query: str
    rationale: str
    recommended_action: str
    used_llm: bool = False


def _fallback_issue(check: RuleCheck) -> LegalIssue:
    hint = LAW_HINTS[check.rule_id]
    return LegalIssue(
        rule_id=check.rule_id,
        search_query=hint.query,
        rationale=check.document_excerpt or check.description,
        recommended_action=check.suggestion or "관련 서류와 내부 기준을 확인하세요.",
        used_llm=False,
    )


def _json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON 객체 없음")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON 객체가 아님")
    return obj


def build_legal_issues(
    documents: list[ParsedDocument],
    checks: list[RuleCheck],
    use_llm: bool = True,
) -> dict[str, LegalIssue]:
    targets = [check for check in checks if check.status != CheckStatus.PASS]
    fallback = {check.rule_id: _fallback_issue(check) for check in checks}
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not use_llm or not api_key or not targets:
        return fallback

    facts = [
        {
            "document_id": document.document_id,
            "doc_type": document.doc_type,
            "fields": {field.name: field.value for field in document.fields if field.value},
        }
        for document in documents
    ]
    findings = [
        {
            "rule_id": check.rule_id,
            "status": check.status.value,
            "description": check.description,
            "evidence": check.document_excerpt,
            "suggestion": check.suggestion,
        }
        for check in targets
    ]
    prompt = f"""당신은 금융상품 판매서류 검증 시스템의 법령 검색 쿼리 생성기입니다.
결정론적 규칙의 판정 상태를 바꾸거나 법률 위반을 확정하지 마세요.
각 rule_id마다 다음만 생성하세요.
- search_query: 국가법령정보/RAG 검색에 사용할 구체적인 한국어 질의
- rationale: 제공된 서류 사실만 이용한 짧은 설명
- recommended_action: 추가 확인 조치

JSON 객체만 출력하세요.
{{"issues":[{{"rule_id":"FIT-001","search_query":"...","rationale":"...","recommended_action":"..."}}]}}

서류 구조화 사실:
{json.dumps(facts, ensure_ascii=False)}

규칙 판정:
{json.dumps(findings, ensure_ascii=False)}
"""

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            max_tokens=1600,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        content = "".join(block.text for block in message.content if hasattr(block, "text"))
        payload = _json_object(content)
        for item in payload.get("issues", []):
            rule_id = str(item.get("rule_id", ""))
            if rule_id not in fallback:
                continue
            query = str(item.get("search_query", "")).strip()
            rationale = str(item.get("rationale", "")).strip()
            action = str(item.get("recommended_action", "")).strip()
            if not query:
                continue
            fallback[rule_id] = LegalIssue(
                rule_id=rule_id,
                search_query=query[:300],
                rationale=(rationale or fallback[rule_id].rationale)[:800],
                recommended_action=(action or fallback[rule_id].recommended_action)[:800],
                used_llm=True,
            )
    except Exception:
        pass
    return fallback
