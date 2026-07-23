"""규정 버전 추적 + 개정 감지 (규정 개정 재검증의 토대).

- 수집한 규정의 시행일자·내용해시를 스냅샷으로 기록.
- 국가법령정보 API의 현행 시행일자와 대조해 개정 여부를 감지.
개정이 감지되면 규정을 재수집하고 서류를 재검증한다(src/verify/reverify.py).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from src.ingest.law_api import LawApiClient, LawApiError

REG_DIR = Path("data/regulations")
SNAPSHOT_PATH = REG_DIR / ".versions.json"


@dataclass
class RegulationVersion:
    source: str
    enforcement_date: str  # 시행일자 YYYYMMDD
    article_count: int
    content_hash: str      # 조문 내용 해시(재수집 시 실제 변경 확인용)


@dataclass
class UpdateStatus:
    source: str
    stored_date: str
    live_date: str
    changed: bool          # 현행 시행일자가 스냅샷과 다르면 True (개정 감지)


def _enforcement_date(raw: dict) -> str:
    """법령(기본정보) / 행정규칙(행정규칙기본정보) 어느 쪽이든 시행일자 추출."""
    for key in ("기본정보", "행정규칙기본정보"):
        info = raw.get(key)
        if isinstance(info, dict) and info.get("시행일자"):
            return str(info["시행일자"]).strip()
    return ""


def _is_admrule(source: str) -> bool:
    return "감독규정" in source or source.endswith("규정")


def snapshot_current(directory: str | Path = REG_DIR) -> dict[str, RegulationVersion]:
    """data/regulations/*.raw.json + *.articles.json 로 현재 버전 스냅샷 생성."""
    directory = Path(directory)
    versions: dict[str, RegulationVersion] = {}
    for raw_path in sorted(directory.glob("*.raw.json")):
        source = raw_path.name[: -len(".raw.json")]
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        art_path = directory / f"{source}.articles.json"
        articles = (
            json.loads(art_path.read_text(encoding="utf-8")) if art_path.exists() else []
        )
        blob = json.dumps(articles, ensure_ascii=False, sort_keys=True).encode("utf-8")
        versions[source] = RegulationVersion(
            source=source,
            enforcement_date=_enforcement_date(raw),
            article_count=len(articles),
            content_hash=hashlib.sha256(blob).hexdigest()[:16],
        )
    return versions


def save_snapshot(versions: dict[str, RegulationVersion], path: str | Path = SNAPSHOT_PATH) -> None:
    Path(path).write_text(
        json.dumps({k: asdict(v) for k, v in versions.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def load_snapshot(path: str | Path = SNAPSHOT_PATH) -> dict[str, RegulationVersion]:
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: RegulationVersion(**v) for k, v in data.items()}


def _live_enforcement_date(client: LawApiClient, source: str) -> str:
    """국가법령정보 API에서 해당 규정의 현행 시행일자를 조회(검색 결과의 시행일자)."""
    try:
        if _is_admrule(source):
            hits = [h for h in client.search_admrules(source) if h.get("행정규칙명") == source]
        else:
            hits = [h for h in client.search_laws(source) if h.get("법령명한글") == source]
    except LawApiError:
        return ""
    return str(hits[0].get("시행일자", "")).strip() if hits else ""


def check_live_updates(
    versions: dict[str, RegulationVersion] | None = None,
) -> list[UpdateStatus]:
    """스냅샷(또는 현재 파일) 대비 현행 시행일자를 조회해 개정 여부를 반환."""
    versions = versions or load_snapshot() or snapshot_current()
    client = LawApiClient()
    out: list[UpdateStatus] = []
    for source, ver in versions.items():
        live = _live_enforcement_date(client, source)
        out.append(
            UpdateStatus(
                source=source,
                stored_date=ver.enforcement_date,
                live_date=live,
                changed=bool(live) and live != ver.enforcement_date,
            )
        )
    return out
