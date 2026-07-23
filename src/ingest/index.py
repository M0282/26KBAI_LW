"""조문 검색 인덱스 — 검증 엔진이 후보 조문을 추리는 검색 계층.

1차 구현은 BM25(단어 + 문자 bigram): 조문 수백 개 규모에서 충분히 정확하고
무거운 임베딩 의존성이 없다. 검증 파이프라인은 이 인덱스로 후보 조문 top-k를
뽑은 뒤 LLM이 정밀 대조한다. 인터페이스(search)를 좁게 유지해 이후
임베딩 기반(chromadb)으로 교체·병행 가능.

사용 예:
    from src.ingest.index import ArticleIndex
    idx = ArticleIndex.from_dir()          # data/regulations/*.articles.json 로드
    hits = idx.search("설명의무", k=5)      # [{source, article_no, title, text, score}]
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

DEFAULT_DIR = Path("data/regulations")


def _tokenize(text: str) -> list[str]:
    """한국어 법률 텍스트용 토크나이저: 어절 + 문자 bigram.

    형태소 분석기 없이도 '설명의무'가 '설명의무를'·'설명의무의' 등과 매칭되도록
    bigram을 병행한다.
    """
    words = re.findall(r"[가-힣A-Za-z0-9]+", text)
    tokens: list[str] = []
    for w in words:
        tokens.append(w)
        tokens.extend(w[i : i + 2] for i in range(len(w) - 1))
    return tokens


class ArticleIndex:
    def __init__(self, articles: list[dict]):
        if not articles:
            raise ValueError("조문이 비어 있습니다. fetch_regulations를 먼저 실행하세요.")
        self.articles = articles
        self._bm25 = BM25Okapi([_tokenize(a["text"]) for a in articles])

    @classmethod
    def from_dir(cls, dir_path: str | Path = DEFAULT_DIR) -> "ArticleIndex":
        """data/regulations/*.articles.json 전체를 하나의 인덱스로 로드."""
        articles: list[dict] = []
        for path in sorted(Path(dir_path).glob("*.articles.json")):
            articles.extend(json.loads(path.read_text(encoding="utf-8")))
        return cls(articles)

    def search(self, query: str, k: int = 5) -> list[dict]:
        """query와 관련 높은 조문 top-k. 각 항목에 score가 추가된다."""
        scores = self._bm25.get_scores(_tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [
            {**self.articles[i], "score": round(float(scores[i]), 3)}
            for i in order[:k]
            if scores[i] > 0
        ]
