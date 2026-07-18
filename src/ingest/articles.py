"""법령/행정규칙 본문 JSON → 조문 단위 청크 변환.

RAG 인덱싱의 입력 단위: 조문 1개 = 청크 1개.
청크 스키마: {source, source_type, article_no, title, text}
- source: 법령/규칙명 (예: "은행법", "은행업감독규정")
- article_no: 조문 번호 문자열 (예: "34", "34의2")
- text: 조문 제목 + 본문 + 항/호/목 전체를 합친 평문
"""
from __future__ import annotations

from typing import Any


def _collect_text(node: Any) -> list[str]:
    """조문 트리에서 '...내용' 키의 문자열을 문서 순서대로 수집한다."""
    out: list[str] = []
    if isinstance(node, str):
        s = node.strip()
        if s:
            out.append(s)
    elif isinstance(node, list):
        for item in node:
            out.extend(_collect_text(item))
    elif isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("내용") or key in ("항", "호", "목"):
                out.extend(_collect_text(value))
    return out


def extract_law_articles(law: dict) -> list[dict]:
    """lawService(target=law) 응답에서 조문 청크를 추출한다."""
    basic = law.get("기본정보", {})
    source = basic.get("법령명_한글") or basic.get("법령명한글") or ""
    units = law.get("조문", {}).get("조문단위")
    if isinstance(units, dict):
        units = [units]

    chunks: list[dict] = []
    for unit in units or []:
        if unit.get("조문여부") != "조문":  # '전문'(장·절 표제) 등은 제외
            continue
        no = str(unit.get("조문번호", ""))
        branch = str(unit.get("조문가지번호", "") or "")
        article_no = f"{no}의{branch}" if branch and branch != "0" else no
        text = "\n".join(_collect_text(unit))
        if not text:
            continue
        chunks.append(
            {
                "source": source,
                "source_type": "law",
                "article_no": article_no,
                "title": str(unit.get("조문제목", "") or ""),
                "text": text,
            }
        )
    return chunks


def extract_admrule_articles(rule: dict, source: str) -> list[dict]:
    """lawService(target=admrul) 응답에서 조문 청크를 추출한다.

    행정규칙 본문은 법령과 구조가 달라 '조문내용' 리스트(조 단위 평문)로 온다.
    각 항목은 보통 "제1조(목적) ..." 형태의 문자열이다.
    """
    body = rule.get("조문내용")
    if body is None and "조문" in rule:
        body = rule["조문"]
    texts = _collect_text(body)

    chunks: list[dict] = []
    for text in texts:
        article_no, title = _parse_article_head(text)
        if not article_no and _is_heading(text):  # 장·절 표제는 조문이 아님
            continue
        chunks.append(
            {
                "source": source,
                "source_type": "admrule",
                "article_no": article_no,
                "title": title,
                "text": text,
            }
        )
    return chunks


def _is_heading(text: str) -> bool:
    """'제2장 인가 및 신고 등', '제3절 삭제' 같은 장·절·편·관 표제 여부."""
    import re

    return bool(re.match(r"\s*제\d+(장|절|편|관)(\s|$)", text))


def _parse_article_head(text: str) -> tuple[str, str]:
    """'제34조의2(경영지도기준) ...' → ("34의2", "경영지도기준"). 실패 시 ("", "")."""
    import re

    m = re.match(r"\s*제(\d+)조(?:의(\d+))?\s*(?:\(([^)]*)\))?", text)
    if not m:
        return "", ""
    article_no = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
    return article_no, m.group(3) or ""
