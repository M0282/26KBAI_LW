"""모델 교차 검증 — 저가 모델(haiku)과 상위 모델(sonnet/opus)의 판정 필드 일치 확인.

"haiku로 충분한가? 비싼 모델을 쓰면 판정이 달라지나?"를 데이터로 답한다.
판정 임계 필드(투자성향·위험등급)는 결정론적 스캔이 확정하므로 모델과 무관하게
같아야 한다. 이 스크립트로 실증한다(발표에서 '저가 모델로 충분' 근거).

실행:
    py -3 -m scripts.eval_cross_model data/eval/*.pdf
    py -3 -m scripts.eval_cross_model --models claude-haiku-4-5,claude-sonnet-5 --dir data/eval

주의: 캐시를 끄고 각 모델로 실제 호출하므로 문서×모델 수만큼 API 비용 발생.
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.parser.financial_extractor import extract_with_llm, field_map
from src.parser.pdf_loader import load_pdf, to_parsed_document

CRITICAL = ["customer_profile", "product_risk_level"]


def _paths(argv: list[str]) -> list[Path]:
    paths: list[Path] = []
    it = iter(argv)
    for a in it:
        if a in ("--dir",):
            paths += [Path(p) for p in glob.glob(f"{next(it, '')}/*.pdf")]
        elif a == "--models":
            next(it, "")
        elif not a.startswith("--"):
            paths += [Path(p) for p in glob.glob(a)]
    return sorted({p for p in paths if p.suffix.lower() == ".pdf"})


def main(argv: list[str]) -> int:
    load_dotenv()
    os.environ["LLM_CACHE"] = "0"
    models = ["claude-haiku-4-5", "claude-sonnet-5"]
    if "--models" in argv:
        models = argv[argv.index("--models") + 1].split(",")
    paths = _paths(argv)
    if not paths:
        print("사용법: py -3 -m scripts.eval_cross_model [--models a,b] [--dir DIR] <pdf...>")
        return 1

    print(f"=== 모델 교차 검증 ({' vs '.join(models)}) — {len(paths)}개 ===\n")
    print(f"{'문서':26} {'필드':16} " + " ".join(f"{m.split('-')[1][:8]:12}" for m in models) + " 일치")
    print("-" * 74)
    mismatch = 0
    for path in paths:
        pdf = load_pdf(str(path))
        parsed = to_parsed_document(pdf)
        results = []
        for m in models:
            os.environ["ANTHROPIC_MODEL"] = m
            results.append(field_map(extract_with_llm(parsed, locator=pdf.locate)))
        for f in CRITICAL:
            vals = [str(r.get(f)) for r in results]
            if all(v == "None" for v in vals):
                continue
            same = len(set(vals)) == 1
            if not same:
                mismatch += 1
            print(f"{path.name[:26]:26} {f:16} "
                  + " ".join(f"{v[:12]:12}" for v in vals)
                  + f" {'✅' if same else '⚠️불일치'}")

    print(f"\n판정 필드 불일치: {mismatch}건 "
          f"({'✅ 모델 무관하게 동일 — 저가 모델로 충분' if mismatch == 0 else '⚠️ 확인 필요'})")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
