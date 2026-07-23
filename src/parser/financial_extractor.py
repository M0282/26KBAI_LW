"""금융상품 판매서류 분류·핵심 필드 추출.

LLM을 우선 사용하되 API 키가 없거나 호출에 실패하면 규칙 기반 폴백으로 동작한다.
LLM이 제시한 근거 문구가 원문에 실제로 존재하지 않으면 해당 추출값을 폐기한다.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

from src.common.schemas import ParsedDocument, ParsedField

Locator = Callable[[str], list[dict]]

DOC_TYPES = {
    "suitability_form": ("적합성 진단표", ["투자성향", "투자자 유형", "적합성", "투자목적"]),
    "product_description": ("상품설명서", ["상품설명서", "위험등급", "원금손실", "수수료", "보수"]),
    "application": ("가입신청서", ["가입신청", "청약", "신청금액", "계약일", "가입일"]),
    "acknowledgement": ("설명 확인서", ["설명 확인", "고객 확인", "설명일", "서명"]),
}

FIELD_PATTERNS: dict[str, list[str]] = {
    "customer_profile": [
        r"(?:투자성향|투자자\s*유형|고객\s*성향)\s*[:：]?\s*([^\n]{1,30})",
    ],
    "product_name": [
        r"(?:상품명|금융상품명|펀드명)\s*[:：]?\s*([^\n]{2,100})",
    ],
    "product_code": [
        r"(?:상품코드|상품\s*코드)\s*[:：]?\s*([A-Za-z0-9_-]{3,40})",
    ],
    "product_risk_level": [
        r"(?:위험등급|상품\s*위험등급)\s*[:：]?\s*([^\n]{1,30})",
    ],
    "explanation_date": [
        r"(?:설명일|설명\s*일자|상품설명일)\s*[:：]?\s*([0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2})",
        r"(?:설명일|설명\s*일자|상품설명일)\s*[:：]?\s*([0-9]{4}년\s*[0-9]{1,2}월\s*[0-9]{1,2}일)",
    ],
    "contract_date": [
        r"(?:계약일|가입일|신청일|청약일)\s*[:：]?\s*([0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2})",
        r"(?:계약일|가입일|신청일|청약일)\s*[:：]?\s*([0-9]{4}년\s*[0-9]{1,2}월\s*[0-9]{1,2}일)",
    ],
    "customer_acknowledgement": [
        r"(?:고객\s*확인|설명\s*확인|확인\s*여부|고객\s*서명)\s*[:：]\s*([^\n]{1,30})",
    ],
    "staff_name": [
        r"(?:설명\s*담당자|담당자|판매직원)\s*[:：]?\s*([^\n]{1,30})",
    ],
}

PROFILE_NORMALIZATION = {
    "안정형": ["안정형", "안정 추구형", "보수형", "원금 보존 우선", "위험 선호 낮음"],
    "안정추구형": ["안정추구형", "안정 성장형"],
    "위험중립형": ["위험중립형", "중립형"],
    "적극투자형": ["적극투자형", "적극형"],
    "공격투자형": ["공격투자형", "공격형", "고위험 선호"],
}

RISK_NORMALIZATION = {
    "1등급": ["1등급", "매우 높은 위험", "최고위험", "고위험"],
    "2등급": ["2등급", "높은 위험"],
    "3등급": ["3등급", "다소 높은 위험"],
    "4등급": ["4등급", "보통 위험"],
    "5등급": ["5등급", "낮은 위험"],
    "6등급": ["6등급", "매우 낮은 위험"],
}

SEMANTIC_EXPLANATION_FIELDS = {
    "principal_loss_explained": [
        "원금손실", "투자원금", "원금의 전부 또는 일부", "예금자보호 대상이 아니",
        "투자금액을 하회", "손실은 투자자에게 귀속",
    ],
    "risk_level_explained": ["위험등급", "위험 수준", "위험도"],
    "fees_explained": ["수수료", "보수", "비용", "판매보수", "운용보수"],
}

ALL_FIELD_NAMES = tuple(FIELD_PATTERNS) + tuple(SEMANTIC_EXPLANATION_FIELDS)


@dataclass
class ExtractionResult:
    doc_type: str
    fields: list[ParsedField]
    used_llm: bool
    warning: str | None = None


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t:：-|,")


def _compact_for_match(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _normalize(value: str, mapping: dict[str, list[str]]) -> str:
    compact = _compact(value).lower()
    for normalized, aliases in mapping.items():
        if any(alias.lower() in compact for alias in aliases):
            return normalized
    return _compact(value)


def _normalize_date(value: str) -> str:
    value = value.strip()
    korean = re.fullmatch(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", value)
    if korean:
        y, m, d = map(int, korean.groups())
        return f"{y:04d}-{m:02d}-{d:02d}"
    parts = re.split(r"[./-]", value)
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        y, m, d = map(int, parts)
        return f"{y:04d}-{m:02d}-{d:02d}"
    return value


def normalize_field(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if name == "customer_profile":
        return _normalize(value, PROFILE_NORMALIZATION)
    if name == "product_risk_level":
        return _normalize(value, RISK_NORMALIZATION)
    if name in {"explanation_date", "contract_date"}:
        return _normalize_date(value)
    if name in SEMANTIC_EXPLANATION_FIELDS:
        lowered = _compact(value).lower()
        return None if lowered in {"false", "아니오", "없음", "미확인", "null", "none"} else "확인"
    return _compact(value)


def classify_document_rule_based(text: str) -> str:
    scores: list[tuple[int, str]] = []
    compact = _compact_for_match(text)
    for doc_type, (_, keywords) in DOC_TYPES.items():
        score = sum(compact.count(_compact_for_match(keyword)) for keyword in keywords)
        scores.append((score, doc_type))
    best_score, best_type = max(scores, default=(0, "unknown"))
    return best_type if best_score > 0 else "unknown"


def _page_from_locator(locator: Locator | None, evidence: str | None) -> int | None:
    if not locator or not evidence:
        return None
    try:
        hits = locator(evidence)
    except Exception:
        return None
    return int(hits[0]["page"]) if hits else None


def _field(name: str, value: str | None, evidence: str | None, confidence: float, locator: Locator | None) -> ParsedField:
    return ParsedField(
        name=name,
        value=normalize_field(name, value),
        page=_page_from_locator(locator, evidence),
        confidence=confidence if value is not None else 0.0,
    )


def extract_rule_based(parsed: ParsedDocument, locator: Locator | None = None) -> ExtractionResult:
    text = parsed.raw_text
    doc_type = classify_document_rule_based(text)
    fields: list[ParsedField] = []

    for name, patterns in FIELD_PATTERNS.items():
        match = next((m for pattern in patterns if (m := re.search(pattern, text, flags=re.IGNORECASE))), None)
        raw_value = _compact(match.group(1)) if match else None
        evidence = match.group(0) if match else None
        fields.append(_field(name, raw_value, evidence, 0.82, locator))

    compact_text = _compact_for_match(text)
    for name, phrases in SEMANTIC_EXPLANATION_FIELDS.items():
        evidence = next((phrase for phrase in phrases if _compact_for_match(phrase) in compact_text), None)
        fields.append(_field(name, "확인" if evidence else None, evidence, 0.75, locator))

    return ExtractionResult(doc_type=doc_type, fields=fields, used_llm=False)


def _llm_prompt(text: str) -> str:
    return f"""당신은 금융상품 판매서류 구조화 도우미입니다.
아래 문서를 읽고 JSON 객체만 출력하세요. 법률 위반 여부는 판단하지 마세요.

허용 doc_type:
suitability_form, product_description, application, acknowledgement, unknown

필드:
customer_profile, product_name, product_code, product_risk_level,
explanation_date, contract_date, customer_acknowledgement, staff_name,
principal_loss_explained, risk_level_explained, fees_explained

날짜는 YYYY-MM-DD로 정규화하세요.
설명 항목 3개는 문서에 해당 의미의 설명이 있으면 true, 없거나 불명확하면 null입니다.
각 필드의 evidence에는 반드시 문서에 실제 존재하는 짧은 원문을 그대로 복사하세요.
근거가 없는 내용은 만들지 말고 null로 두세요.

출력 형식:
{{
  "doc_type": "...",
  "fields": {{"customer_profile": null}},
  "evidence": {{"customer_profile": null}}
}}

문서:
{text[:30000]}
"""


def _extract_json_object(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM 응답에 JSON 객체가 없습니다.")
    payload = json.loads(content[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM 응답 JSON이 객체가 아닙니다.")
    return payload


def extract_with_llm(parsed: ParsedDocument, locator: Locator | None = None) -> ExtractionResult:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        result = extract_rule_based(parsed, locator=locator)
        result.warning = "ANTHROPIC_API_KEY가 없어 규칙 기반 추출을 사용했습니다."
        return result

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            max_tokens=1800,
            temperature=0,
            messages=[{"role": "user", "content": _llm_prompt(parsed.raw_text)}],
        )
        content = "".join(block.text for block in message.content if hasattr(block, "text"))
        payload = _extract_json_object(content)
        values = payload.get("fields") or {}
        evidence = payload.get("evidence") or {}
        fields: list[ParsedField] = []
        normalized_text = _compact_for_match(parsed.raw_text)

        for name in ALL_FIELD_NAMES:
            raw_value = values.get(name)
            excerpt = evidence.get(name)
            if excerpt is not None:
                excerpt = str(excerpt).strip()
            # 환각 방지: 제시한 근거가 원문에 없으면 값과 근거 모두 폐기.
            if excerpt and _compact_for_match(excerpt) not in normalized_text:
                raw_value = None
                excerpt = None
            value = str(raw_value) if raw_value is not None else None
            fields.append(_field(name, value, excerpt, 0.95, locator))

        doc_type = str(payload.get("doc_type", "unknown"))
        if doc_type not in DOC_TYPES and doc_type != "unknown":
            doc_type = "unknown"
        return ExtractionResult(doc_type=doc_type, fields=fields, used_llm=True)
    except Exception as exc:
        result = extract_rule_based(parsed, locator=locator)
        result.warning = f"LLM 추출 실패로 규칙 기반 폴백 사용: {type(exc).__name__}"
        return result


def extract_document(parsed: ParsedDocument, use_llm: bool = True, locator: Locator | None = None) -> ExtractionResult:
    return extract_with_llm(parsed, locator=locator) if use_llm else extract_rule_based(parsed, locator=locator)


def enrich_document(parsed: ParsedDocument, use_llm: bool = True, locator: Locator | None = None) -> ParsedDocument:
    result = extract_document(parsed, use_llm=use_llm, locator=locator)
    return ParsedDocument(
        document_id=parsed.document_id,
        doc_type=result.doc_type,
        fields=result.fields,
        raw_text=parsed.raw_text,
    )


def field_map(document: ParsedDocument) -> dict[str, str | None]:
    return {field.name: field.value for field in document.fields}


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
