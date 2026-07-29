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

# 문서유형별로 '나올 수 있는' 필드를 고정한다.
#
# 왜: 같은 양식의 서류 2건이 서로 다른 필드 목록을 내놓으면 도구를 신뢰할 수 없다.
# 실측 — 문장 133개가 동일한 핵심요약설명서 2건에서 한 건만 customer_acknowledgement가
# 나왔다. 그 문서의 '확인'은 전부 안내 문구("□ 설명의무 이행확인", "서명을 하거나…")이고
# 실제 고객 확인 기록이 아니었다. 즉 값의 유무가 문서가 아니라 LLM 변덕으로 갈렸다.
#
# 유형에 없는 필드는 아예 담지 않고, 있는 필드는 값이 없어도 자리를 남긴다(미확인).
# 상품설명서에 고객확인·계약일이 없는 것은 문서의 성격이지 추출 실패가 아니다.
DOC_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "suitability_form": (
        "customer_profile", "explanation_date", "customer_acknowledgement",
        "staff_name", "principal_loss_explained", "risk_level_explained",
    ),
    "product_description": (
        "product_name", "product_code", "product_risk_level",
        "principal_loss_explained", "risk_level_explained", "fees_explained",
    ),
    "application": (
        "product_name", "product_code", "contract_date", "customer_acknowledgement",
        "staff_name", "principal_loss_explained", "fees_explained",
    ),
    "acknowledgement": (
        "product_name", "explanation_date", "contract_date", "customer_acknowledgement",
        "staff_name", "principal_loss_explained", "risk_level_explained", "fees_explained",
    ),
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
    # '비용'·'보수'는 두 글자짜리 일반어라 엉뚱한 문맥에도 걸린다. 실측 —
    # "본 안내장 제작 비용은 당사가 부담합니다", "담당자: 김보수" 만으로
    # 수수료 설명이 이행됐다고 판정했다(EXP-001 미탐, 금소법 19조).
    # 실물 상품설명서는 수수료를 33회·보수를 28회 쓰는데 전부 복합어
    # (판매수수료·판매보수·총보수)라 좁혀도 탐지에는 지장이 없다.
    "fees_explained": [
        "수수료", "판매보수", "운용보수", "수탁보수", "총보수", "보수율", "제비용",
    ],
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
        # 두 자리 연도는 세기를 지어내지 않고 원문 그대로 둔다. 그래야 뒤에서
        # 날짜로 읽히지 않아 '형식 확인' 경고가 뜬다.
        # 예전에는 "26.07.15"를 서기 26년으로 만들어 버렸다 — 그러면 설명일이
        # 계약일보다 2000년 앞서게 되어, 계약 이후 설명(DATE-001 위반)을
        # 정상으로 통과시킨다(실측: 설명일 26.07.15 / 계약일 2026-07-12 → PASS).
        if len(parts[0]) != 4:
            return value
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


def _compact_with_map(text: str) -> tuple[str, list[int]]:
    """공백을 제거한 문자열과, 각 문자의 원문 인덱스 대응표."""
    chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            index_map.append(i)
    return "".join(chars), index_map


def ground_product_name(name: str | None, text: str) -> str | None:
    """상품명을 원문에 실제로 존재하는 표기로 교정한다.

    실측: 원문이 '신한금융투자 제 23129호 파생결합증권(ELS) (원금비보장형)'인데
    LLM이 '제'를 빼고 '(주가연계증권)'을 지어 넣었다. 이런 이름은 하이라이트가
    불가능할 뿐 아니라, PKG-001(문서 간 상품 동일성)이 이 값으로 비교하므로
    서류가 같은 상품인데도 다르다고 판정될 수 있다.

    원문에 그대로 있으면 그대로 두고, 없으면 원문에서 가장 길게 겹치는 구간을
    찾아 그 '원문 표기'로 바꾼다. 겹침이 너무 짧으면 지어낸 값으로 보고 버린다.
    """
    if not name:
        return name
    compact_text, index_map = _compact_with_map(text)
    compact_name = re.sub(r"\s+", "", name)
    if not compact_name or compact_name in compact_text:
        return name

    best_start = best_len = 0
    for start in range(len(compact_name)):
        # 이미 찾은 것보다 길어질 수 없으면 중단
        if len(compact_name) - start <= best_len:
            break
        for end in range(len(compact_name), start + best_len, -1):
            if compact_name[start:end] in compact_text:
                best_start, best_len = start, end - start
                break

    # 기준은 '원문에서 얼마나 복원했는가'다. LLM이 덧붙인 군더더기까지 분모로 삼으면
    # 멀쩡한 복원까지 버리게 된다(실측: 14자를 복원했는데 임계값 15에 걸려 폐기).
    if best_len < max(10, len(compact_name) // 3):
        return None
    fragment = compact_name[best_start : best_start + best_len]
    pos = compact_text.find(fragment)
    recovered = text[index_map[pos] : index_map[pos + best_len - 1] + 1]
    return recovered.strip(" ()[]{}·,:;-") or None


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


def _vision_ask(image_bytes: bytes, prompt: str, tag: str, max_tokens: int = 16) -> str | None:
    """페이지 이미지에 짧은 질문을 던진다. 결과 캐시로 같은 페이지 재호출은 0원."""
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
    key = make_key(tag, model, hashlib.sha256(image_bytes).hexdigest())

    def _produce() -> str:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(image_bytes).decode(),
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return "".join(b.text for b in message.content if b.type == "text")

    try:
        return cached_text(key, _produce)
    except Exception:
        return None


def vision_scan_risk_grade(image_bytes: bytes) -> str | None:
    """페이지 이미지에서 부여된 위험등급을 읽는다(텍스트에 값이 없을 때만 호출).

    실측: 은행 핵심요약설명서는 위험등급을 범례표 체크(✓)로만 표시해
    텍스트 레이어가 공란이다. 이 경로가 없으면 등급이 영영 안 잡힌다.
    """
    answer = _vision_ask(image_bytes, _VISION_GRADE_PROMPT, "vision-grade")
    m = re.search(r"[1-6]", answer or "")
    return f"{m.group(0)}등급" if m else None


_VISION_SIGNATURE_PROMPT = (
    "이 페이지에 고객(저축자·투자자)의 서명 또는 기명날인이 실제로 기재되어 있습니까? "
    "개인정보는 절대 출력하지 마세요. 서명란이 있고 채워져 있으면 '기재됨', "
    "서명란은 있으나 비어 있으면 '공란', 서명란 자체가 없으면 '서명란없음'. "
    "이 셋 중 하나만 출력하세요."
)

SIGNED = "확인(서명 기재)"
UNSIGNED = "미서명"
# 서류가 스스로 '확인받지 못했다'고 적어둔 표현. ACK-001의 부정 판정어와 같은 집합.
_UNSIGNED_TOKENS = ("미확인", "미서명", "미기재", "아니오", "공란", "빈칸")
# '없음'은 무엇이 없는지까지 봐야 한다. 그냥 부분 문자열로 찾으면 '특이사항 없음',
# '이의 없음 확인 서명'처럼 정상 서명 문구를 미서명으로 판정한다(실측: 오탐 3건).
# 컴플라이언스 도구에서 오탐은 미탐만큼 나쁘다 — 정상 건이 빨간불이면 안 쓰게 된다.
_UNSIGNED_PHRASES = ("서명없음", "확인없음", "기재없음", "날인없음", "서명란없음")


def states_unsigned(value: str) -> bool:
    """서류가 스스로 '확인받지 못했다'고 적은 표현인지.

    추출(정규화)과 판정(ACK-001·DOC-001)이 같은 기준을 써야 한다.
    한쪽만 고치면 같은 서류에 모순된 판정이 나온다(실측).
    """
    compact = value.replace(" ", "")
    if compact.lower() in ("false", "no"):  # LLM이 불리언으로 내는 경우
        return True
    if compact in ("없음", "무", "-"):  # 값 자체가 '없음'이면 서명이 없다는 뜻
        return True
    if any(token in compact for token in _UNSIGNED_TOKENS):
        return True
    return any(phrase in compact for phrase in _UNSIGNED_PHRASES)


def vision_scan_signature(image_bytes: bytes) -> str | None:
    """페이지에 고객 서명이 실제로 기재됐는지 본다.

    실측: 계약서의 '서명' 언급은 전부 약관 조문이고 서명란은 텍스트상 공란인데
    LLM은 그 문구만 읽고 customer_acknowledgement=True를 냈다. 서명이 실제로는
    2쪽에 그림으로 있었으므로 결과는 맞았지만 근거가 틀렸다 — 미서명 서류였어도
    똑같이 True가 나왔을 것이고, 그건 ACK-001이 잡아야 할 위반이다.
    """
    answer = (_vision_ask(image_bytes, _VISION_SIGNATURE_PROMPT, "vision-sign") or "").strip()
    if "기재" in answer:
        return SIGNED
    if "공란" in answer:
        return UNSIGNED
    return None  # 서명란없음 / 판독 실패


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
    if name == "customer_acknowledgement":
        # 서류가 '확인받지 못했다'고 적은 표현은 표기가 제각각이다(미서명 / False / 없음).
        # ACK-001은 부정 표현을 보고 위험을 내므로, 여기서 표준 문구로 모아준다.
        # (실측: LLM이 '미서명'을 'False'로 내보내 규칙이 부정으로 못 읽고 통과시켰다)
        return UNSIGNED if states_unsigned(value) else _compact(value)
    if name in {"explanation_date", "contract_date"}:
        return _normalize_date(value)
    if name in SEMANTIC_EXPLANATION_FIELDS:
        lowered = _compact(value).lower()
        return None if lowered in {"false", "아니오", "없음", "미확인", "null", "none"} else "확인"
    return _compact(value)


def classify_scores(text: str) -> dict[str, int]:
    """문서유형별 키워드 매칭 횟수."""
    compact = _compact_for_match(text)
    return {
        doc_type: sum(compact.count(_compact_for_match(keyword)) for keyword in keywords)
        for doc_type, (_, keywords) in DOC_TYPES.items()
    }


def classify_document_rule_based(text: str) -> str:
    scores = classify_scores(text)
    best_type = max(scores, key=lambda k: scores[k], default="unknown")
    return best_type if scores.get(best_type, 0) > 0 else "unknown"


def confident_rule_doc_type(text: str) -> str | None:
    """규칙 분류가 '이견 없이' 하나를 가리킬 때만 그 유형을 반환한다.

    doc_type은 모든 필드 게이팅의 기준이라 판정 임계값이다. 실측 — 제목이
    '상품설명 확인서'인 설명확인서를 LLM이 상품설명서로 오분류했고, 그 결과
    고객확인·담당자 필드가 스키마에서 통째로 버려져 ACK-001이 위험에서
    누락으로 약해졌다. 반면 규칙 분류는 키워드 4개로 정확히 맞혔다.
    다른 유형 점수가 0이고 자기 점수가 2 이상일 때만 '확신'으로 본다.
    """
    scores = classify_scores(text)
    best_type = max(scores, key=lambda k: scores[k], default="unknown")
    best_score = scores.get(best_type, 0)
    others = [s for t, s in scores.items() if t != best_type]
    return best_type if best_score >= 2 and not any(others) else None


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
    # 규칙 분류가 확신할 때는 LLM보다 우선한다(등급·투자성향에 적용한 원칙과 동일).
    confident = confident_rule_doc_type(parsed.raw_text)
    if confident and confident != doc_type:
        doc_type = confident

    result = ExtractionResult(doc_type=doc_type, fields=fields, used_llm=True)
    _apply_doc_type_gating(result, parsed)
    return result


def _apply_doc_type_gating(result: ExtractionResult, parsed: ParsedDocument) -> None:
    """문서유형에 따라 값의 채택·폐기를 결정한다.

    유형이 바뀌면(사용자 교정 포함) 이 규칙을 다시 적용해야 한다.
    유형별로 '그 서류에 있을 수 없는 값'을 비우는 것이 핵심이다.
    """
    doc_type = result.doc_type
    by_name = {f.name: f for f in result.fields}

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
    # 상품명: 원문에 있는 표기로 교정한다(하이라이트·상품 동일성 판정의 기준값).
    nf = by_name.get("product_name")
    if nf is not None and nf.value:
        grounded = ground_product_name(nf.value, parsed.raw_text)
        if grounded != nf.value:
            nf.value = grounded
            nf.confidence = 0.7 if grounded else 0.0
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


def apply_doc_type_schema(result: ExtractionResult) -> None:
    """추출 결과를 문서유형의 고정 필드 목록으로 맞춘다.

    같은 유형이면 값이 무엇이든 **항상 같은 필드 목록**이 나오게 하는 것이 목적이다.
    유형 밖 필드는 버리고(오탐 차단), 유형 안 필드는 없으면 빈 값으로 자리를 만든다.
    """
    expected = DOC_TYPE_FIELDS.get(result.doc_type)
    if not expected:
        return
    by_name = {field.name: field for field in result.fields}
    result.fields = [
        by_name.get(name) or ParsedField(name=name, value=None, page=None, confidence=0.0)
        for name in expected
    ]


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


_VISION_CONTRACT_DATE_PROMPT = (
    "이 서류에 기재된 계약 체결일(신청일)을 YYYY-MM-DD 형식으로만 출력하세요. "
    "기재되어 있지 않으면 '없음'. 다른 말은 하지 마세요."
)


def ground_scanned_date(answer: str | None, text: str) -> str | None:
    """비전이 읽은 날짜를 원문 숫자와 대조해 채택 여부를 정한다.

    비전이 지어낸 날짜를 그대로 쓰지 않도록, 연·월·일 숫자가 모두 원문에
    존재할 때만 채택한다(위험등급·상품명에 적용한 원문 대조와 같은 원칙).
    """
    m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", answer or "")
    if not m:
        return None
    year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    compact = re.sub(r"\s+", "", text)
    for part in (year, f"{month:02d}", f"{day:02d}"):
        if part not in compact:
            return None  # 원문에 없는 숫자 → 환각으로 보고 폐기
    return f"{year}-{month:02d}-{day:02d}"


def vision_scan_contract_date(image_bytes: bytes, text: str) -> str | None:
    """페이지 이미지에서 계약일을 읽고, 그 숫자가 원문에도 있는지 대조한다.

    실측: 계약서의 날짜는 표 양식이라 텍스트 추출이 '년 월 일 24 07 2026'처럼
    라벨과 값을 분리·역순으로 내놓는다. 어떤 날짜 정규식으로도 파싱되지 않아
    DATE-001이 한 번도 판정된 적이 없었다.
    """
    answer = _vision_ask(image_bytes, _VISION_CONTRACT_DATE_PROMPT, "vision-cdate", max_tokens=24)
    return ground_scanned_date(answer, text)


_DATE_DOC_TYPES = ("application", "acknowledgement")
_ACK_DOC_TYPES = ("application", "acknowledgement", "suitability_form")
_VISION_SIGN_MAX_PAGES = 4  # 서명란은 보통 앞·뒤 몇 쪽 안에 있다. 비용 상한.


# 한 페이지에서 물어볼 수 있는 항목들. 질문마다 따로 호출하면 같은 이미지를
# 여러 번 전송하게 된다(실측: 계약서 2쪽짜리에 비전 호출 4회 → 이미지 토큰 4배).
# 비용의 대부분이 이미지 토큰(장당 ~1,600)이므로 한 번에 모아 묻는다.
_VISION_ITEM_SPECS = {
    "risk_grade": (
        '"risk_grade": 이 상품에 부여된 위험등급 숫자(1~6). '
        "표에 체크·색칠·동그라미로 표시된 등급이나 'N등급으로 분류' 문구를 근거로 하고, "
        "없으면 null"
    ),
    "contract_date": (
        '"contract_date": 계약 체결일(신청일)을 "YYYY-MM-DD" 문자열로. 없으면 null'
    ),
    "signature": (
        '"signature": 고객(저축자·투자자)의 서명·기명날인 상태. '
        '기재돼 있으면 "signed", 서명란은 있으나 비어 있으면 "blank", 서명란 자체가 없으면 null'
    ),
}


def vision_read_page(image_bytes: bytes, items: tuple[str, ...]) -> dict:
    """페이지 이미지 한 장에 필요한 항목을 한 번에 묻는다.

    개인정보는 요청하지 않는다 — 성명·주소·계좌는 판정에 쓰이지 않으므로
    애초에 응답에 담기지 않게 해 캐시에도 남지 않도록 한다.
    """
    if not items:
        return {}
    prompt = (
        "이 금융 서류 페이지 이미지를 보고 아래 항목만 JSON 객체로 출력하세요.\n"
        "개인정보(성명·주민등록번호·계좌번호·주소·연락처)는 절대 출력하지 마세요.\n"
        "보이지 않거나 판단할 수 없으면 null. JSON 외 다른 말은 하지 마세요.\n\n"
        + "\n".join(_VISION_ITEM_SPECS[i] for i in items if i in _VISION_ITEM_SPECS)
    )
    answer = _vision_ask(
        image_bytes, prompt, "vision-page:" + ",".join(sorted(items)), max_tokens=200
    )
    if not answer:
        return {}
    try:
        return _extract_json_object(answer)
    except (ValueError, TypeError):
        return {}


def _fill_from_vision(
    result: ExtractionResult, parsed: ParsedDocument, renderer: PageRenderer
) -> None:
    """텍스트로 못 얻은 판정 필드를 페이지 그림에서 한 번에 읽어 채운다.

    페이지당 호출 1회. 필요한 항목이 모두 채워지면 즉시 중단한다.
    """
    by_name = {f.name: f for f in result.fields}
    wanted: list[str] = []
    if result.doc_type == "product_description" and not (
        by_name.get("product_risk_level") and by_name["product_risk_level"].value
    ):
        wanted.append("risk_grade")
    if result.doc_type in _DATE_DOC_TYPES and not (
        by_name.get("contract_date") and by_name["contract_date"].value
    ):
        wanted.append("contract_date")
    if result.doc_type in _ACK_DOC_TYPES and "customer_acknowledgement" in by_name:
        wanted.append("signature")
    if not wanted:
        return

    signature_verdict: str | None = None
    for page_number in range(1, _VISION_SIGN_MAX_PAGES + 1):
        if not wanted:
            break
        image = renderer(page_number)
        if not image:
            break
        payload = vision_read_page(image, tuple(wanted))

        if "risk_grade" in wanted:
            m = re.search(r"[1-6]", str(payload.get("risk_grade") or ""))
            if m:
                field = by_name["product_risk_level"]
                field.value, field.confidence, field.page = f"{m.group(0)}등급", 0.85, page_number
                wanted.remove("risk_grade")

        if "contract_date" in wanted:
            value = ground_scanned_date(str(payload.get("contract_date") or ""), parsed.raw_text)
            if value:
                field = by_name["contract_date"]
                field.value, field.confidence, field.page = value, 0.85, page_number
                wanted.remove("contract_date")

        if "signature" in wanted:
            answer = str(payload.get("signature") or "").strip().lower()
            if answer == "signed":
                signature_verdict = SIGNED
                by_name["customer_acknowledgement"].page = page_number
                wanted.remove("signature")
            elif answer == "blank":
                # 서명란은 있는데 비어 있음. 뒷장에서 서명을 찾으면 그쪽이 우선이므로 계속 본다.
                signature_verdict = UNSIGNED

    if "customer_acknowledgement" in by_name and result.doc_type in _ACK_DOC_TYPES:
        field = by_name["customer_acknowledgement"]
        if signature_verdict is not None:
            field.value, field.confidence = signature_verdict, 0.85
        elif field.value and not states_unsigned(field.value):
            # 서명란을 찾지 못했는데 텍스트만 보고 '확인'을 낸 값은 근거가 없다.
            # 다만 '미서명'처럼 서류가 명시적으로 부정을 적어둔 경우는 그 자체가
            # 증거이므로 지우지 않는다(지우면 위험이 누락으로 약해진다).
            field.value, field.confidence = None, 0.0


def extract_document(
    parsed: ParsedDocument,
    use_llm: bool = True,
    locator: Locator | None = None,
    page_renderer: PageRenderer | None = None,
    force_doc_type: str | None = None,
) -> ExtractionResult:
    """서류 1건을 판독·추출한다.

    force_doc_type: 검토자가 화면에서 문서유형을 교정한 경우 그 값을 사용한다.
    실물 서류는 어휘가 섞여 있어 규칙 분류가 확신하지 못하고(실측 22건 전부 침묵),
    유형 판단이 전적으로 LLM에 달려 있다. 유형 하나가 틀리면 위험등급·날짜·고객확인
    게이팅이 전부 어긋나 판정이 조용히 약해지므로, 사람이 고칠 수 있어야 한다.
    """
    if force_doc_type in DOC_TYPES:
        # 유형을 확정한 뒤 추출해야 필드 게이팅·스캔 우선순위가 그 유형 기준으로 걸린다.
        parsed = ParsedDocument(
            document_id=parsed.document_id, doc_type=force_doc_type,
            fields=parsed.fields, raw_text=parsed.raw_text,
        )
    result = (
        extract_with_llm(parsed, locator=locator) if use_llm else extract_rule_based(parsed, locator=locator)
    )
    if force_doc_type in DOC_TYPES:
        result.doc_type = force_doc_type
        _apply_doc_type_gating(result, parsed)
    # 텍스트에 값이 없고 그림에만 있는 항목(체크표시 등급·표 안의 계약일·서명)을
    # 페이지당 한 번의 호출로 모아서 보완한다.
    if page_renderer is not None:
        _fill_from_vision(result, parsed, page_renderer)
    # 마지막에 유형별 고정 스키마로 맞춘다 — 같은 양식이면 같은 필드 목록이 나온다.
    apply_doc_type_schema(result)
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
