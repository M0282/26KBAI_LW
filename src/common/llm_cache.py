"""LLM 응답 결과 캐시 — 반복 호출 비용을 0으로.

친구 아키텍처(작은 지시문 + 큰 서류원문)에서는 프롬프트 캐싱(프리픽스 캐시)의
이득이 거의 없다. 대신 **같은 입력이면 저장된 응답을 재사용**하는 결과 캐시가
실질 절감이 크다:
- 개발 중 같은 서류로 반복 검증 → 2회차부터 API 0회
- 발표 시연 서류를 사전 실행해 저장 → 발표 중 API 0회, 지연 0초

키는 (모델 + 프롬프트 전체)의 sha256. 파일 하나당 JSON으로 디스크에 저장한다.
LLM_CACHE=0 으로 끌 수 있다(항상 새로 호출).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

CACHE_DIR = Path(os.environ.get("LLM_CACHE_DIR", ".cache/llm"))


def _enabled() -> bool:
    return os.environ.get("LLM_CACHE", "1") != "0"


def make_key(*parts: str) -> str:
    """모델·프롬프트 등을 이어붙여 sha256 키를 만든다."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def cached_text(key: str, produce: Callable[[], str]) -> str:
    """key에 해당하는 LLM 텍스트 응답이 있으면 재사용, 없으면 produce()로 생성·저장.

    캐시 파일이 깨져 있으면 무시하고 새로 호출한다(안전).
    """
    if not _enabled():
        return produce()
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))["text"]
        except (OSError, ValueError, KeyError):
            pass
    text = produce()
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 캐시 저장 실패는 치명적이지 않음
    return text
