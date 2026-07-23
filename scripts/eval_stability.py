"""LLM 추출 재현성(결정성) 검증 — 판정 필드가 실행마다 흔들리지 않는지 확인.

캐시를 끄고 같은 문서를 N회 추출해 필드 일관성을 본다.
모델·프롬프트를 바꾼 뒤 판정 임계 필드(위험등급 등)의 결정성을 회귀 검증하는 용도.

실행:
    py -3 -m scripts.eval_stability data/eval/*.pdf
    py -3 -m scripts.eval_stability --runs 5 --dir data/eval

핵심: 위험등급·설명 등은 결정론적 후처리(스캔/전체원문)로 일관돼야 한다.
상품명 등 서술 필드는 LLM 특성상 미세 변동이 있을 수 있다(상품코드로 식별하므로 영향 낮음).
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.parser.financial_extractor import extract_with_llm, field_map
from src.parser.pdf_loader import load_pdf, to_parsed_document

WATCH = ["product_name", "product_code", "product_risk_level",
         "principal_loss_explained", "fees_explained"]


def _paths(argv: list[str]) -> list[Path]:
    paths: list[Path] = []
    it = iter(argv)
    for a in it:
        if a == "--dir":
            paths += [Path(p) for p in glob.glob(f"{next(it, '')}/*.pdf")]
        elif not a.startswith("--"):
            paths += [Path(p) for p in glob.glob(a)]
    return sorted({p for p in paths if p.suffix.lower() == ".pdf"})


def main(argv: list[str]) -> int:
    load_dotenv()
    os.environ["LLM_CACHE"] = "0"  # 재현성 측정 위해 캐시 강제 OFF
    runs = 3
    if "--runs" in argv:
        runs = int(argv[argv.index("--runs") + 1])
    paths = _paths(argv)
    if not paths:
        print("사용법: py -3 -m scripts.eval_stability [--runs N] [--dir DIR] <pdf...>")
        return 1

    print(f"=== 추출 재현성 검증 ({runs}회, 캐시 OFF) — {len(paths)}개 ===\n")
    unstable_total = 0
    for path in paths:
        pdf = load_pdf(str(path))
        parsed = to_parsed_document(pdf)
        maps = [field_map(extract_with_llm(parsed, locator=pdf.locate)) for _ in range(runs)]
        unstable = [k for k in WATCH if len({str(m.get(k)) for m in maps}) > 1]
        crit = [k for k in unstable if k in ("product_risk_level", "principal_loss_explained", "fees_explained")]
        mark = "✅ 판정필드 안정" if not crit else f"🚨 판정필드 불안정: {crit}"
        note = f" (서술필드 변동: {unstable})" if unstable and not crit else ""
        print(f"{path.name[:34]:34} {mark}{note}")
        unstable_total += len(crit)

    print(f"\n판정 임계 필드 불안정: {unstable_total}건 "
          f"({'✅ 전부 결정론적' if unstable_total == 0 else '🚨 확인 필요'})")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
