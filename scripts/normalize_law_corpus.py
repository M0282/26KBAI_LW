"""이미 받아 둔 조문 JSON에서 제목 중복을 걷어낸다.

`articles.py` 는 앞으로 받을 조문에 대해 고쳐졌지만, 커밋해 둔 코퍼스는
그 전에 만들어졌다. 다시 받으려면 국가법령정보 API 인증이 필요하므로
같은 규칙을 파일에 직접 적용한다.

실행:
    py -3.12 -m scripts.normalize_law_corpus          # 무엇이 바뀌는지만 출력
    py -3.12 -m scripts.normalize_law_corpus --write  # 실제로 저장
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.ingest.articles import drop_heading_echo

CORPUS = Path("data/regulations")


def main(write: bool) -> int:
    files = sorted(CORPUS.glob("*.articles.json"))
    if not files:
        print(f"{CORPUS} 에 조문 파일이 없습니다.")
        return 1

    total_changed = 0
    for path in files:
        items = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        for item in items:
            original = item.get("text") or ""
            fragments = [line for line in original.split("\n") if line.strip()]
            cleaned = "\n".join(drop_heading_echo(fragments))
            if cleaned == original:
                continue
            if not cleaned.strip():
                # 본문이 통째로 사라지는 변경은 받아들이지 않는다.
                print(f"  건너뜀(본문이 비게 됨): {path.name} 제{item.get('article_no')}조")
                continue
            item["text"] = cleaned
            changed += 1
        total_changed += changed
        print(f"{path.name}: 조문 {len(items)}건 중 {changed}건 정리")
        if write and changed:
            path.write_text(
                json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

    tail = "  → 저장했습니다." if write else "  (--write 를 주면 저장)"
    print(f"\n합계 {total_changed}건{tail}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main("--write" in sys.argv))
