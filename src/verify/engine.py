"""검증 엔진 — 파이프라인의 심장 (Track A).

입력: 파싱된 판매 서류들(list[ParsedDocument]) + 조문 검색 인덱스(ArticleIndex)
출력: VerificationReport (누락/위험 판정 + 근거 조문 + 근거 문장)

하이브리드 설계:
- 규칙(코드): 문서 간 교차 검증(적합성 진단표 ↔ 상품설명서), 근거 텍스트 존재 검증
- LLM: 서류가 각 금소법 의무를 충족하는지 의미 판단 + 근거 조문/문장 선택

교차 검증은 우리 핵심 차별화 — 단일 문서로는 못 잡는 '안정형 고객 ↔ 고위험 상품'
같은 적합성원칙(금소법 17조) 위반을 두 문서 대조로 적발한다.
"""
from __future__ import annotations

import re

from src.common.schemas import (
    CheckStatus,
    ParsedDocument,
    RuleCheck,
    VerificationReport,
)
from src.ingest.index import ArticleIndex
from src.verify.llm import LLMClient, get_client

# LLM이 서류별로 판단할 금소법 핵심 의무 (검색 질의로도 사용)
OBLIGATIONS = [
    ("설명의무", "원금 손실 위험 등 상품 핵심 내용 설명의무"),
    ("적합성원칙", "고객 투자성향에 적합하지 않은 상품 권유 금지"),
    ("부당권유금지", "불확실한 사항 단정적 판단 제공 등 부당권유행위 금지"),
    ("광고규제", "금융상품 광고 필수 준수사항"),
]

_LLM_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "obligation": {"type": "string"},
                    "satisfied": {"type": "boolean"},
                    "evidence_text": {"type": "string"},
                    "reason": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["obligation", "satisfied", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["checks"],
    "additionalProperties": False,
}


class VerifyEngine:
    def __init__(self, index: ArticleIndex, llm: LLMClient | None = None):
        self.index = index
        self.llm = llm or get_client()

    # ---- 공개 API ---------------------------------------------------------
    def verify(self, docs: list[ParsedDocument]) -> VerificationReport:
        """판매 서류 패키지 전체를 검증해 하나의 리포트로 반환."""
        checks: list[RuleCheck] = []
        checks.extend(self._cross_document_suitability(docs))
        for doc in docs:
            checks.extend(self._llm_obligation_checks(doc))

        primary = docs[0].document_id if docs else "package"
        doc_type = "; ".join(sorted({d.doc_type for d in docs})) or "unknown"
        return VerificationReport(
            document_id=primary,
            doc_type=doc_type,
            checks=checks,
            summary=self._summarize(checks),
        )

    # ---- 규칙 기반: 문서 간 교차 검증 -------------------------------------
    def _cross_document_suitability(self, docs: list[ParsedDocument]) -> list[RuleCheck]:
        """적합성 진단표의 투자성향 ↔ 상품설명서의 위험등급 정합성.

        '안정형' 고객에게 '고위험' 상품 → 금소법 17조(적합성원칙) 위반 소지.
        """
        tendency = self._find_field(docs, ("투자성향", "고객투자성향", "성향"))
        risk = self._find_field(docs, ("위험등급", "상품위험등급"))
        if not tendency or not risk:
            return []

        conservative = any(k in tendency.value for k in ("안정", "안정형", "보수"))
        high_risk = any(k in risk.value for k in ("고위험", "1등급", "매우 높은", "높은 위험"))
        if not (conservative and high_risk):
            return []

        article = self._top_article("적합성 원칙 투자성향에 적합하지 않은 상품 권유 금지")
        return [
            RuleCheck(
                rule_id="CROSS-SUITABILITY",
                description="적합성 진단표의 투자성향과 상품설명서의 위험등급 정합성",
                status=CheckStatus.RISK,
                evidence_clause=article["clause"] if article else None,
                evidence_text=article["text"] if article else None,
                document_excerpt=f"투자성향='{tendency.value}' ↔ 위험등급='{risk.value}'",
                suggestion="투자성향에 부적합한 고위험 상품 권유입니다. 적합성 원칙 위반 여부를 확인하고 "
                "판매 적정성 재검토 또는 부적합 확인서 징구가 필요합니다.",
            )
        ]

    # ---- LLM 기반: 서류별 의무 충족 판단 ---------------------------------
    def _llm_obligation_checks(self, doc: ParsedDocument) -> list[RuleCheck]:
        # system = 고정 지시 + 참고 조문 (모든 서류에 동일 → 캐시 프리픽스).
        # 조문은 OBLIGATIONS·인덱스가 고정이라 매 호출 바이트 동일 → 2회차부터 캐시 적중.
        system = self._obligation_system_prompt()
        # prompt = 가변(서류 원문)만. 프리픽스를 깨지 않도록 뒤에 둔다.
        prompt = (
            f"[검토 대상 서류: {doc.doc_type}]\n{doc.raw_text[:6000]}\n\n"
            "위 참고 조문을 근거로 다음 의무별 충족 여부를 판단하라: "
            + ", ".join(k for k, _ in OBLIGATIONS)
        )
        try:
            result = self.llm.complete_json(system, prompt, _LLM_CHECK_SCHEMA)
        except Exception:
            return []

        checks: list[RuleCheck] = []
        for item in result.get("checks", []):
            if item.get("satisfied", True):
                continue  # 충족한 항목은 리포트에 넣지 않음(문제만 표시)
            evidence = item.get("evidence_text") or ""
            # 환각 방지: LLM이 인용한 근거가 원문에 실제 존재하는지 확인
            if evidence and not self._evidence_exists(doc.raw_text, evidence):
                evidence = ""  # 존재하지 않으면 근거 제거(판정은 유지하되 근거 미표시)
            article = self._top_article(item.get("obligation", ""))
            checks.append(
                RuleCheck(
                    rule_id=f"LLM-{item.get('obligation', 'OBLIGATION')}",
                    description=f"{item.get('obligation')} 충족 여부",
                    status=CheckStatus.MISSING,
                    evidence_clause=article["clause"] if article else None,
                    evidence_text=article["text"] if article else None,
                    document_excerpt=evidence or None,
                    suggestion=item.get("suggestion") or item.get("reason"),
                )
            )
        return checks

    def _obligation_system_prompt(self) -> str:
        """고정 시스템 프롬프트(지시 + 참고 조문). 캐시 프리픽스로 재사용된다."""
        blocks = []
        for _key, desc in OBLIGATIONS:
            for h in self.index.search(desc, k=2):
                blocks.append(
                    f"[{h['source']} 제{h['article_no']}조 {h['title']}]\n{h['text'][:400]}"
                )
        return (
            "당신은 금융소비자보호법 준수 여부를 검토하는 컴플라이언스 보조 AI다. "
            "주어진 판매 서류가 각 의무를 충족하는지 '서류에 실제로 존재하는 근거'만으로 판단한다. "
            "근거 문장(evidence_text)은 반드시 서류 원문에서 그대로 인용한다. 지어내지 않는다.\n\n"
            "[참고 조문]\n" + "\n\n".join(blocks)
        )

    # ---- 헬퍼 ------------------------------------------------------------
    @staticmethod
    def _find_field(docs: list[ParsedDocument], keys: tuple[str, ...]):
        for doc in docs:
            for f in doc.fields:
                if f.value and any(k in f.name for k in keys):
                    return f
        return None

    def _top_article(self, query: str) -> dict | None:
        hits = self.index.search(query, k=1)
        if not hits:
            return None
        h = hits[0]
        return {"clause": f"{h['source']} 제{h['article_no']}조({h['title']})", "text": h["text"]}

    @staticmethod
    def _evidence_exists(source_text: str, evidence: str) -> bool:
        """LLM이 인용한 근거 문장이 원문에 실제 존재하는지(공백 무시) 확인."""
        norm_src = re.sub(r"\s+", "", source_text)
        norm_ev = re.sub(r"\s+", "", evidence)
        if not norm_ev:
            return False
        return norm_ev in norm_src or (len(norm_ev) >= 20 and norm_ev[:20] in norm_src)

    @staticmethod
    def _summarize(checks: list[RuleCheck]) -> str:
        risk = sum(c.status == CheckStatus.RISK for c in checks)
        missing = sum(c.status == CheckStatus.MISSING for c in checks)
        if not checks:
            return "발견된 위반·누락 사항이 없습니다."
        parts = []
        if risk:
            parts.append(f"위험 {risk}건")
        if missing:
            parts.append(f"누락 {missing}건")
        return "적합성/의무 검토 결과 " + ", ".join(parts) + " 적발 — 판매 적정성 확인 필요."
