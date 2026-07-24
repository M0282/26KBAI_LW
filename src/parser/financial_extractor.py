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


_RISK_GRADE_RE = re.compile(r"[1-6]\s*등급")
# 실물 상품설명서는 "(투자) 위험 등급  N등급(…위험)" 형태로 위험등급을 명시한다.
# LLM/규칙이 놓쳐도 전체 원문에서 이 패턴으로 보완 추출한다(결정론적·무료).
_RISK_GRADE_SCAN = re.compile(r"위험\s*등급[^0-9]{0,10}?([1-6])\s*등급")
# 운용사 상품설명서의 표준 문구: "…변동성을 감안하여 6등급으로 분류하였습니다".
# 라벨('투자위험등급')과 값 사이에 설명 문장이 길게 끼어 위 근접 스캔이 놓치던 형태다.
# '분류/부여/결정'이라는 확정 동사가 붙으므로 범례표의 등급 나열과 혼동되지 않는다.
_RISK_GRADE_DECLARED = re.compile(r"([1-6])등급(?:으로|을)?(?:분류|부여|결정|산정)")
# PDF 줄바꿈이 단어 한가운데를 자른다(실측: "6등급으로 분 류하였습니다").
# 공백을 지운 사본에서 매칭해야 이런 문서를 놓치지 않는다.
_WHITESPACE = re.compile(r"\s+")


def scan_risk_grade(text: str) -> str | None:
    """문서에 명시된 위험등급을 결정론적으로 뽑는다(LLM보다 우선).

    확정 문구("N등급으로 분류")를 근접 스캔보다 먼저 본다. 근접 스캔은 범례표가
    있는 문서에서 잘못된 등급을 집을 여지가 있으나, 확정 문구는 그 위험이 없다.
    """
    m = _RISK_GRADE_DECLARED.search(_WHITESPACE.sub("", text))
    if m:
        return f"{m.group(1)}등급"
    m = _RISK_GRADE_SCAN.search(text)
    return f"{m.group(1)}등급" if m else None


def has_grade_legend(text: str) -> bool:
    """1~6등급을 모두 나열한 범례표가 있는 문서인지.

    범례가 있으면 원문에 모든 등급 숫자가 존재하므로 '원문에 있는가' 검증이
    무력해진다(어떤 값이든 통과). 이런 문서는 LLM 숫자를 믿지 않고
    확정문구 스캔이나 비전 판독으로만 등급을 정한다.
    """
    return len(set(re.findall(r"([1-6])\s*등급", text))) >= 5


_VISION_GRADE_PROMPT = (
    "이 금융상품 서류에서 '이 상품에 부여된 위험등급'이 몇 등급인지만 판단하세요. "
    "표에 체크(✓)·색칠·동그라미로 표시된 등급이 있으면 그 등급입니다. "
    "설명 문구에 'N등급으로 분류'라고 적혀 있으면 그 등급입니다. "
    "표시가 전혀 없으면 '없음'이라고 답하세요. "
    "1~6 숫자 하나 또는 '없음'만 출력하고 다른 말은 하지 마세요."
)


def vision_scan_risk_grade(image_bytes: bytes) -> str | None:
    """페이지 이미지에서 부여된 위험등급을 읽는다(텍스트에 값이 없을 때만 호출).

    실측: 은행 핵심요약설명서는 위험등급을 범례표 체크(✓)로만 표시해
    텍스트 레이어가 공란이다. 이 경로가 없으면 등급이 영영 안 잡힌다.
    결과 캐시로 같은 페이지 재호출은 0원. VISION_OCR=0이면 비활성.
    """
    if os.environ.get("VISION_OCR", "1") == "0" or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import base64
        import hashlib

        import anthropic

        from src.common.llm_cache import cached_text, make_key
    except Exception:
        return None
    model = os.environ.get("VISION_MODEL", "claude-haiku-4-5")
    key = make_key("vision-grade", model, hashlib.sha256(image_bytes).hexdigest())

    def _produce() -> str:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(image_bytes).decode(),
                    }},
                    {"type": "text", "text": _VISION_GRADE_PROMPT},
                ],
            }],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    try:
        answer = cached_text(key, _produce)
    except Exception:
        return None
    m = re.search(r"[1-6]", answer or "")
    return f"{m.group(0)}등급" if m else None


def grade_supported_by_text(grade: str | None, text: str) -> bool:
    """추출된 위험등급이 원문에 실제로 존재하는지 확인(환각 차단).

    실측: 원문에 '6등급'만 있는 상품설명서에서 LLM이 '5등급'을 냈고, 그 값으로
    적합성 판정까지 내려갔다. 문서에 없는 등급은 근거 하이라이트도 불가능하므로
    판정에 쓰면 안 된다.
    """
    if not grade:
        return False
    m = re.match(r"\s*([1-6])", grade)
    if not m:
        return False
    n = m.group(1)
    compact = _WHITESPACE.sub("", text)  # 줄바꿈으로 잘린 표기도 인정
    return bool(
        re.search(rf"{n}등급", compact)
        or re.search(rf"위험등급[^0-9]{{0,20}}{n}(?![0-9])", compact)
    )


# 적합성 진단표의 투자성향 명시 문구("투자성향은 '위험중립형'", "투자성향 : 안정형").
# 위험도 범례(직전/최종 투자성향 나열)를 오인하지 않도록 '은' 또는 ':' 뒤만 매칭.
_PROFILE_SCAN = re.compile(
    r"투자성향(?:은|\s*[:：])\s*['\"]?\s*(공격투자형|적극투자형|위험중립형|안정추구형|안정형)"
)


def scan_customer_profile(text: str) -> str | None:
    m = _PROFILE_SCAN.search(text)
    return m.group(1) if m else None


def normalize_field(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    # 판정 임계 필드(투자성향·위험등급)는 표준값으로 인식될 때만 채택한다.
    # 실물 문서에서 규칙 정규식이 엉뚱한 문장을 매칭해 '쓰레기값'을 내는 것을 차단
    # → 인식 실패 시 None(빈값)으로 두어 오판 대신 재검토(LLM 승격/미확인)로 넘긴다.
    if name == "customer_profile":
        norm = _normalize(value, PROFILE_NORMALIZATION)
        return norm if norm in PROFILE_NORMALIZATION else None
    if name == "product_risk_level":
        norm = _normalize(value, RISK_NORMALIZATION)
        return norm if (norm in RISK_NORMALIZATION or _RISK_GRADE_RE.search(norm)) else None
    if name == "product_name":
        # 조사·접속어로 시작하면 규칙 정규식이 엉뚱한 문장을 잡은 것 → 폐기
        compact = _compact(value)
        if compact.startswith(("및 ", "에 ", "의 ", "을 ", "를 ", "이 ", "가 ", "뿐만", "들이 ", "으로 ")):
            return None
        return compact
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

    # 위험등급: 상품설명서에서만 명시 라벨 스캔 우선(진단표 위험도 범례 오인 방지).
    risk_field = next((f for f in fields if f.name == "product_risk_level"), None)
    if risk_field is not None:
        if doc_type == "product_description":
            grade = scan_risk_grade(text)
            if grade:
                risk_field.value = grade
                risk_field.confidence = 0.9
            elif has_grade_legend(text) or not grade_supported_by_text(risk_field.value, text):
                risk_field.value = None
                risk_field.confidence = 0.0
        else:
            risk_field.value = None
    # 날짜: 상품설명서에는 계약일·설명일이 없다(발행일·기준일 오인 방지).
    if doc_type == "product_description":
        for field_item in fields:
            if field_item.name in ("contract_date", "explanation_date"):
                field_item.value = None
                field_item.confidence = 0.0
    # 투자성향: 적합성 진단표에서 명시 문구 스캔.
    prof_field = next((f for f in fields if f.name == "customer_profile"), None)
    if prof_field is not None and doc_type == "suitability_form":
        prof = scan_customer_profile(text)
        if prof:
            prof_field.value = prof
            prof_field.confidence = 0.9

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


def _model_ladder() -> list[str]:
    """추출 모델 승격 사다리.

    - ANTHROPIC_MODEL 을 명시하면 그 모델만 사용(자동 승격 안 함).
    - LLM_ESCALATE=0 이면 haiku 단일.
    - 기본: haiku-4-5 → (실패 시) opus-4-8. sonnet은 실측상 haiku 실패를
      구제하지 못해 건너뛴다(호출 낭비 방지). '어려운 것만 Opus' 계획과 일치.
    """
    forced = os.environ.get("ANTHROPIC_MODEL")
    if forced:
        return [forced]
    if os.environ.get("LLM_ESCALATE", "1") == "0":
        return ["claude-haiku-4-5"]
    return ["claude-haiku-4-5", "claude-opus-4-8"]


def _is_weak(result: ExtractionResult) -> bool:
    """저렴한 모델이 명백히 실패했는지(= 승격 필요) 판정.

    분류 실패(unknown)이면서 의미 있는 필드를 하나도 못 뽑은 경우만 승격한다.
    (부분 추출은 승격하지 않아 불필요한 상위 모델 호출을 막는다)
    """
    has_value = any(f.value for f in result.fields)
    return result.doc_type == "unknown" and not has_value


def _attempt_llm(
    parsed: ParsedDocument, model: str, api_key: str, locator: Locator | None
) -> ExtractionResult:
    """단일 모델로 1회 추출 시도. 실패 시 예외를 올린다(상위에서 폴백/승격 처리)."""
    import anthropic

    from src.common.llm_cache import cached_text, make_key

    prompt = _llm_prompt(parsed.raw_text)

    def _call() -> str:
        client = anthropic.Anthropic(api_key=api_key)
        # temperature 미지정: 최신 모델(Sonnet 5·Opus 4.8 등)은 sampling 파라미터를 받지 않는다(400).
        message = client.messages.create(
            model=model,
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in message.content if hasattr(block, "text"))

    # 결과 캐시: 같은 서류+모델이면 API 재호출 없이 저장된 응답 재사용(개발·데모 비용 0)
    content = cached_text(make_key("extract", model, prompt), _call)
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

    # 긴 문서 보완: LLM 프롬프트는 원문을 30k자로 절단하므로 뒷페이지의 설명(원금손실·
    # 수수료 등)을 놓칠 수 있다. 설명 존재 여부는 전체 원문 구절 스캔으로 보완한다.
    by_name = {f.name: f for f in fields}
    for name, phrases in SEMANTIC_EXPLANATION_FIELDS.items():
        f = by_name.get(name)
        if f is not None and f.value is None:
            if any(_compact_for_match(p) in normalized_text for p in phrases):
                f.value = "확인"
                f.confidence = 0.7
    doc_type = str(payload.get("doc_type", "unknown"))
    if doc_type not in DOC_TYPES and doc_type != "unknown":
        doc_type = "unknown"

    # 위험등급: '상품설명서'에서만 명시 라벨을 권위로 삼는다(LLM 오추출 잦음).
    # 진단표 등은 위험도 범례를 상품등급으로 오인하지 않도록 위험등급을 비운다.
    rf = by_name.get("product_risk_level")
    if rf is not None:
        if doc_type == "product_description":
            grade = scan_risk_grade(parsed.raw_text)
            if grade:
                rf.value = grade
                rf.confidence = 0.9
            elif has_grade_legend(parsed.raw_text) or not grade_supported_by_text(
                rf.value, parsed.raw_text
            ):
                # 원문에 없는 등급 = 환각. 범례표가 있으면 원문 존재 검증이 무력하므로
                # 역시 신뢰하지 않는다. 오판보다 '미확인'이 안전하다(이후 비전이 채운다).
                rf.value = None
                rf.confidence = 0.0
        else:
            rf.value = None
    # 날짜: 상품설명서(간이투자설명서 포함)에는 계약일·설명일이 없다.
    # 발행일·기준일을 계약일로 오인하면 DATE-001이 실행마다 흔들린다(실측).
    if doc_type == "product_description":
        for name in ("contract_date", "explanation_date"):
            df = by_name.get(name)
            if df is not None:
                df.value = None
                df.confidence = 0.0
    # 투자성향: '적합성 진단표'에서 명시 문구를 권위로 삼는다.
    pf = by_name.get("customer_profile")
    if pf is not None and doc_type == "suitability_form":
        prof = scan_customer_profile(parsed.raw_text)
        if prof:
            pf.value = prof
            pf.confidence = 0.9

    return ExtractionResult(doc_type=doc_type, fields=fields, used_llm=True)


def extract_with_llm(parsed: ParsedDocument, locator: Locator | None = None) -> ExtractionResult:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        result = extract_rule_based(parsed, locator=locator)
        result.warning = "ANTHROPIC_API_KEY가 없어 규칙 기반 추출을 사용했습니다."
        return result

    ladder = _model_ladder()
    best: ExtractionResult | None = None
    last_error: Exception | None = None
    for i, model in enumerate(ladder):
        try:
            result = _attempt_llm(parsed, model, api_key, locator)
        except Exception as exc:  # 호출/파싱 실패 → 다음 티어 시도
            last_error = exc
            continue
        best = result
        # 마지막 티어이거나 결과가 충분하면 종료. 약하면 다음(상위) 모델로 승격.
        if i == len(ladder) - 1 or not _is_weak(result):
            if i > 0:
                result.warning = f"저가 모델 추출이 약해 {model}로 승격했습니다."
            return result

    if best is not None:
        return best
    # 모든 티어 실패 → 규칙 기반 폴백
    result = extract_rule_based(parsed, locator=locator)
    result.warning = (
        f"LLM 추출 실패로 규칙 기반 폴백 사용: {type(last_error).__name__}"
        if last_error
        else "LLM 추출 실패로 규칙 기반 폴백 사용"
    )
    return result


PageRenderer = Callable[[int], "bytes | None"]
_VISION_GRADE_MAX_PAGES = 2  # 위험등급은 앞쪽에 있다. 비용 상한을 둔다.


def _fill_risk_grade_from_vision(
    result: ExtractionResult, parsed: ParsedDocument, renderer: PageRenderer
) -> None:
    """상품설명서인데 텍스트에서 위험등급을 못 얻었으면 페이지 그림에서 읽는다."""
    if result.doc_type != "product_description":
        return
    field = next((f for f in result.fields if f.name == "product_risk_level"), None)
    if field is None or field.value:
        return
    for page_number in range(1, _VISION_GRADE_MAX_PAGES + 1):
        image = renderer(page_number)
        if not image:
            break
        grade = vision_scan_risk_grade(image)
        if grade:
            field.value = grade
            field.confidence = 0.85
            field.page = page_number
            return


def extract_document(
    parsed: ParsedDocument,
    use_llm: bool = True,
    locator: Locator | None = None,
    page_renderer: PageRenderer | None = None,
) -> ExtractionResult:
    result = (
        extract_with_llm(parsed, locator=locator) if use_llm else extract_rule_based(parsed, locator=locator)
    )
    # 텍스트에 값이 없고 그림에만 있는 위험등급(체크표시 양식)을 마지막으로 보완한다.
    if page_renderer is not None:
        _fill_risk_grade_from_vision(result, parsed, page_renderer)
    return result


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
