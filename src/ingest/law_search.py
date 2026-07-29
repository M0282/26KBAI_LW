"""로컬 법령 BM25 검색 + 국가법령정보센터 Open API의 조문 원문 보강."""
from __future__ import annotations

import json
import re
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


# "제16조 삭제 <2016.7.28>" 처럼 본문이 폐지 표시뿐인 조문.
# 제목이 붙는 형태("제N조(리스크관리조직) 삭제")도 있다.
_REPEALED = re.compile(
    r"^(?:제\d+조(?:의\d+)?)?\s*(?:\([^)]*\))?\s*삭제\s*(?:<[^>]*>)?\s*$"
)


def is_repealed(chunk: dict) -> bool:
    """폐지된 조문인지. 폐지 조문을 근거로 제시하면 그 판정은 통째로 틀린다."""
    return bool(_REPEALED.fullmatch((chunk.get("text") or "").strip()))


def search_chunks(
    query: str,
    chunks: Iterable[dict],
    preferred_articles: Iterable[str] = (),
    preferred_sources: Iterable[str] = (),
    top_k: int = 5,
    origin: str = "local",
) -> list[LawSearchResult]:
    # 폐지된 조문은 근거가 될 수 없으므로 후보에서 아예 뺀다.
    # 실행 중 검색어는 LLM이 자유 형식으로 만들기 때문에, 규칙에 박아 둔 질의로만
    # 확인해서는 안전하다고 할 수 없다 — 실제로 '삭제된 조항' 질의에서
    # 은행업감독규정 제16조(삭제)가 상위 5건에 들어왔다.
    corpus = [chunk for chunk in chunks if not is_repealed(chunk)]
    scores = _score_chunks(query, corpus)
    article_set = {str(value) for value in preferred_articles}
    source_set = {str(value) for value in preferred_sources}
    results: list[LawSearchResult] = []

    preferred: list[LawSearchResult] = []

    for chunk, base_score in zip(corpus, scores):
        source = str(chunk.get("source", ""))
        article_no = str(chunk.get("article_no", ""))
        score = base_score
        if source in source_set:
            score += 2.5
        result = LawSearchResult(
            source=source,
            source_type=str(chunk.get("source_type", "")),
            article_no=article_no,
            title=str(chunk.get("title", "")),
            text=str(chunk.get("text", "")),
            score=score,
            origin=origin,
        )
        # 규칙마다 근거 조문을 손으로 매핑해 두었다. 그 조문은 점수와 무관하게 먼저 보여준다.
        # 가산점 방식으로는 BM25 점수가 높은 다른 조문에 밀린다 — 실측: 녹취 의무(REC-001)의
        # 최우선 근거로 28조(자료의 기록) 대신 18조(적정성원칙)가 표시됐다.
        # 출처까지 지정됐다면 그 출처의 조문만 우선한다(같은 조 번호가 여러 법령에 있다).
        if article_no in article_set and (not source_set or source in source_set):
            preferred.append(result)
        elif score > 0:
            results.append(result)

    preferred.sort(key=lambda item: item.score, reverse=True)
    results.sort(key=lambda item: item.score, reverse=True)
    return (preferred + results)[:top_k]


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
    merged = _deduplicate([*live, *local], top_k=top_k * 2)
    # 라이브 결과를 앞에 붙이면 큐레이션한 근거 조문이 밀린다(실측: 녹취 의무의
    # 최우선 근거가 28조 대신 18조로 표시됨). 병합 후에도 지정 조문을 먼저 둔다.
    article_set = {str(value) for value in preferred_articles}
    if article_set:
        merged.sort(key=lambda item: item.article_no not in article_set)
    return merged[:top_k]
