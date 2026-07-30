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
        if not self.article_no:
            return self.source
        # 가지조문은 "16의2" 로 저장된다(articles.py 스키마). 그대로 제{번호}조 로
        # 감싸면 "제16의2조" 가 되는데, 올바른 표기는 "제16조의2" 다.
        # 근거 조문을 내세우는 도구에서 조문 번호를 틀리게 쓰면 신뢰를 잃는다
        # (실측: 방문판매 질의에 제16조의2·제21조의2가 잘못된 표기로 노출됨).
        main, _, branch = str(self.article_no).partition("의")
        article = f"제{main}조의{branch}" if branch else f"제{main}조"
        return f"{self.source} {article}"


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
    source_list = [str(value) for value in preferred_sources]
    source_set = set(source_list)
    # 지정 조문 번호는 주 출처에만 적용한다(아래 참고).
    primary_source = source_list[0] if source_list else ""
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
        #
        # 다만 조문 번호는 **주 출처(첫 번째로 지정한 법령)에만** 적용한다. 법률과
        # 감독규정은 번호 체계가 달라 같은 번호가 전혀 다른 내용이다. 두 출처에
        # 모두 적용하면 번호만 같은 감독규정 조문이 인위적으로 상위에 올라온다
        # (실측: 참고 조문 자리를 감독규정 제21조 '계약서류의 제공의무'가 8회 차지 —
        #  ADV-001의 지정 조문이 21조라는 이유만으로).
        if article_no in article_set and (not primary_source or source == primary_source):
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
    focus: Iterable[str] = (),
) -> list[LawSearchResult]:
    """근거 조문 후보를 순위대로 돌려준다.

    focus: 이 규칙이 조문 안에서 걸리는 문구. 조문 번호가 같아도 법률과
        감독규정은 내용이 전혀 다르므로, 번호만으로 최우선을 정하면 엉뚱한
        조문이 올라온다(실측: DOC-001의 최우선 근거로 금소법 제23조
        '계약서류의 제공의무' 대신 감독규정 제23조 '중개업자의 고지의무'가
        표시됐다 — 화면에는 '최우선 근거'라면서 '연결되는 문구를 찾지
        못했다'고 함께 뜨는 자기모순이었다).
        그 규칙과 실제로 연결되는 문구가 있는 조문을 먼저 둔다.
    """
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
    return _rank_basis(merged, preferred_articles, focus)[:top_k]


def _rank_basis(
    results: list[LawSearchResult],
    preferred_articles: Iterable[str],
    focus: Iterable[str],
) -> list[LawSearchResult]:
    """근거 조문 순위: 규칙과 연결되는 문구가 있는 것 → 지정 조문 → 점수.

    라이브 결과를 앞에 붙이면 큐레이션한 근거가 밀린다(실측: 녹취 의무의
    최우선 근거가 28조 대신 18조로 표시됨). 그래서 병합 뒤 다시 정렬한다.
    """
    article_set = {str(value) for value in preferred_articles}
    pattern = _focus_pattern(focus)
    # 정렬은 안정적이므로 점수 순서는 같은 등급 안에서 보존된다.
    return sorted(
        results,
        key=lambda item: (
            not (pattern.search(item.text or "") if pattern else False),
            item.article_no not in article_set if article_set else False,
        ),
    )


def _focus_pattern(focus: Iterable[str]) -> "re.Pattern[str] | None":
    """조문 안에서 규칙이 걸리는 문구를 찾는 정규식(글자 사이 공백 허용).

    조문 원문은 줄바꿈이 낱말 한가운데를 자르는 경우가 있다.
    """
    alternatives = [
        r"\s*".join(re.escape(ch) for ch in phrase if not ch.isspace())
        for phrase in focus
        if phrase and phrase.strip()
    ]
    return re.compile("|".join(alternatives)) if alternatives else None
