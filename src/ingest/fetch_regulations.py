"""검증 근거 규정 일괄 수집 스크립트.

실행 (레포 루트에서):
    py -3 -m src.ingest.fetch_regulations

결과: data/regulations/ 아래에 저장 (git 미추적 — 각자 로컬에서 실행)
    - <이름>.raw.json    원본 응답
    - <이름>.articles.json  조문 청크 (RAG 인덱싱 입력)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.ingest.articles import extract_admrule_articles, extract_law_articles
from src.ingest.law_api import LawApiClient

OUT_DIR = Path("data/regulations")

# 수집 대상: (표시이름, 종류, 검색어) — 검색 결과에서 이름이 정확히 일치하는 항목을 선택
TARGETS = [
    ("은행법", "law", "은행법"),
    ("은행법 시행령", "law", "은행법 시행령"),
    ("은행업감독규정", "admrule", "은행업감독규정"),
    ("금융소비자 보호에 관한 법률", "law", "금융소비자 보호에 관한 법률"),
    ("금융소비자 보호에 관한 감독규정", "admrule", "금융소비자 보호에 관한 감독규정"),
]


def _save(name: str, suffix: str, obj) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.{suffix}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def fetch_all() -> None:
    client = LawApiClient()
    total = 0
    for name, kind, query in TARGETS:
        if kind == "law":
            hits = [h for h in client.search_laws(query) if h.get("법령명한글") == name]
            if not hits:
                print(f"[건너뜀] {name}: 검색 결과 없음")
                continue
            raw = client.get_law(str(hits[0]["법령일련번호"]))
            articles = extract_law_articles(raw)
        else:
            hits = [
                h for h in client.search_admrules(query) if h.get("행정규칙명") == name
            ]
            if not hits:
                print(f"[건너뜀] {name}: 검색 결과 없음")
                continue
            raw = client.get_admrule(str(hits[0]["행정규칙일련번호"]))
            articles = extract_admrule_articles(raw, source=name)

        _save(name, "raw", raw)
        path = _save(name, "articles", articles)
        total += len(articles)
        print(f"[완료] {name}: 조문 {len(articles)}개 → {path}")

    print(f"\n총 {total}개 조문 수집 완료")


if __name__ == "__main__":
    load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")
    fetch_all()
