"""로컬 법령 BM25 검색 + 국가법령정보센터 Open API의 조문 원문 보강."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from src.ingest.articles import extract_admrule_articles, extract_law_articles
from src.ingest.index import bm25_scores
from src.ingest.law_api import LawApiClient, LawApiError


@dataclass(frozen=True)
class LawSearchResult:
    source: str
    source_type: str
    article_no: str
    title: str
    text: str
    score: float
    origin: str = "local"

    @property
    def citation(self) -> str:
        article = f" 제{self.article_no}조" if self.article_no else ""
        return f"{self.source}{article}"


def load_article_chunks(directory: str | Path = "data/regulations") -> list[dict]:
    root = Path(directory)
    chunks: list[dict] = []
    if not root.exists():
        return chunks
    for path in root.glob("*.articles.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                chunks.extend(item for item in data if isinstance(item, dict))
        except (OSError, json.JSONDecodeError):
            continue
    return chunks


def _score_chunks(query: str, corpus: list[dict]) -> list[float]:
    # index.py의 공용 BM25 스코어러 재사용 (어절+bigram 토크나이저 공유, 중복 제거)
    texts = [" ".join(str(chunk.get(k, "")) for k in ("source", "title", "text")) for chunk in corpus]
    return bm25_scores(query, texts)


def search_chunks(
    query: str,
    chunks: Iterable[dict],
    preferred_articles: Iterable[str] = (),
    preferred_sources: Iterable[str] = (),
    top_k: int = 5,
    origin: str = "local",
) -> list[LawSearchResult]:
    corpus = list(chunks)
    scores = _score_chunks(query, corpus)
    article_set = {str(value) for value in preferred_articles}
    source_set = {str(value) for value in preferred_sources}
    results: list[LawSearchResult] = []

    for chunk, base_score in zip(corpus, scores):
        source = str(chunk.get("source", ""))
        article_no = str(chunk.get("article_no", ""))
        score = base_score
        if article_no in article_set:
            score += 5.0
        if source in source_set:
            score += 2.5
        if score <= 0:
            continue
        results.append(
            LawSearchResult(
                source=source,
                source_type=str(chunk.get("source_type", "")),
                article_no=article_no,
                title=str(chunk.get("title", "")),
                text=str(chunk.get("text", "")),
                score=score,
                origin=origin,
            )
        )
    return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]


def search_local_laws(
    query: str,
    chunks: Iterable[dict] | None = None,
    preferred_articles: Iterable[str] = (),
    preferred_sources: Iterable[str] = (),
    top_k: int = 5,
) -> list[LawSearchResult]:
    return search_chunks(
        query,
        list(chunks) if chunks is not None else load_article_chunks(),
        preferred_articles=preferred_articles,
        preferred_sources=preferred_sources,
        top_k=top_k,
        origin="local",
    )


@lru_cache(maxsize=16)
def _fetch_live_source(source: str) -> tuple[dict, ...]:
    client = LawApiClient()
    if "감독규정" in source or source.endswith("규정"):
        hits = [item for item in client.search_admrules(source) if item.get("행정규칙명") == source]
        if not hits:
            return ()
        raw = client.get_admrule(str(hits[0]["행정규칙일련번호"]))
        return tuple(extract_admrule_articles(raw, source=source))
    hits = [item for item in client.search_laws(source) if item.get("법령명한글") == source]
    if not hits:
        return ()
    raw = client.get_law(str(hits[0]["법령일련번호"]))
    return tuple(extract_law_articles(raw))


def search_live_laws(
    query: str,
    preferred_articles: Iterable[str] = (),
    preferred_sources: Iterable[str] = (),
    top_k: int = 5,
) -> list[LawSearchResult]:
    sources = tuple(preferred_sources) or ("금융소비자 보호에 관한 법률", "금융소비자 보호에 관한 감독규정")
    try:
        chunks = [chunk for source in sources for chunk in _fetch_live_source(source)]
    except (LawApiError, OSError, ValueError, KeyError):
        return []
    return search_chunks(
        query,
        chunks,
        preferred_articles=preferred_articles,
        preferred_sources=sources,
        top_k=top_k,
        origin="law.go.kr",
    )


def _deduplicate(results: Iterable[LawSearchResult], top_k: int) -> list[LawSearchResult]:
    selected: dict[tuple[str, str], LawSearchResult] = {}
    for result in results:
        key = (result.source, result.article_no)
        previous = selected.get(key)
        if previous is None or result.score > previous.score or result.origin == "law.go.kr":
            selected[key] = result
    return sorted(selected.values(), key=lambda item: item.score, reverse=True)[:top_k]


def find_legal_basis(
    query: str,
    preferred_articles: Iterable[str] = (),
    preferred_sources: Iterable[str] = (),
    top_k: int = 3,
    allow_live: bool = True,
) -> list[LawSearchResult]:
    local = search_local_laws(
        query,
        preferred_articles=preferred_articles,
        preferred_sources=preferred_sources,
        top_k=top_k,
    )
    if not allow_live:
        return local
    live = search_live_laws(
        query,
        preferred_articles=preferred_articles,
        preferred_sources=preferred_sources,
        top_k=top_k,
    )
    return _deduplicate([*live, *local], top_k=top_k)
