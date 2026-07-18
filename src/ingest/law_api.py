"""국가법령정보센터 Open API 클라이언트.

- 인증: .env 의 LAW_API_OC (이메일 아이디). https://open.law.go.kr 에서 신청.
- 주의: 검색어 인코딩이 깨져도 API는 에러 없이 totalCnt=0을 반환한다(조용한 실패).
  requests가 UTF-8 percent-encoding을 자동 처리하므로 이 클라이언트를 통해서만 호출할 것.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests

SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "http://www.law.go.kr/DRF/lawService.do"


class LawApiError(RuntimeError):
    """법령 API 호출 실패 (비JSON 응답, 인증 실패 등)."""


class LawApiClient:
    def __init__(self, oc: Optional[str] = None, timeout: int = 30):
        self.oc = oc or os.environ.get("LAW_API_OC", "")
        if not self.oc:
            raise LawApiError("LAW_API_OC가 설정되지 않았습니다. .env를 확인하세요.")
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any]) -> dict:
        resp = requests.get(
            url, params={"OC": self.oc, "type": "JSON", **params}, timeout=self.timeout
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as e:
            raise LawApiError(
                f"JSON이 아닌 응답 (OC 미승인 또는 파라미터 오류 가능): {resp.text[:200]}"
            ) from e

    @staticmethod
    def _as_list(value: Any) -> list[dict]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def search_laws(self, query: str) -> list[dict]:
        """현행법령 검색. 각 항목의 '법령일련번호'(MST)로 본문을 조회한다."""
        data = self._get(SEARCH_URL, {"target": "law", "query": query, "display": 100})
        return self._as_list(data.get("LawSearch", {}).get("law"))

    def search_admrules(self, query: str) -> list[dict]:
        """행정규칙(금융위 고시·감독규정 등) 검색. '행정규칙일련번호'로 본문 조회."""
        data = self._get(SEARCH_URL, {"target": "admrul", "query": query, "display": 100})
        return self._as_list(data.get("AdmRulSearch", {}).get("admrul"))

    def get_law(self, mst: str) -> dict:
        """법령 본문(조문 단위 구조 포함) 조회. mst = 검색 결과의 법령일련번호."""
        data = self._get(SERVICE_URL, {"target": "law", "MST": mst})
        return data.get("법령", data)

    def get_admrule(self, rule_id: str) -> dict:
        """행정규칙 본문 조회. rule_id = 검색 결과의 행정규칙일련번호."""
        data = self._get(SERVICE_URL, {"target": "admrul", "ID": rule_id})
        return data.get("AdmRulService", data)
