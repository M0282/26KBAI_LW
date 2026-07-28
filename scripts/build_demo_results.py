"""데모 판독 결과 사전 생성 — 제출본을 받은 사람이 키 없이 시연할 수 있게 한다.

키를 가진 사람(우리)이 한 번 실행해 결과를 data/demo_results/ 에 저장하면,
키가 없는 환경에서도 같은 파일을 올렸을 때 저장된 판독 결과가 재생된다.

실행:
    py -3 -m scripts.make_demo_package    # 데모 서류 4종 생성(먼저)
    py -3 -m scripts.build_demo_results   # 판독 결과 저장 (API 키 필요, 1회)

기본 대상은 데모 패키지다. 다른 파일을 함께 담고 싶으면 인자로 경로를 준다.
개인정보가 든 실제 고객 서류는 담지 말 것 — 저장 파일에 추출값이 남는다.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.common.demo_store import STORE_DIR, save_result
from src.parser.financial_extractor import extract_document, field_map
from src.parser.pdf_loader import load_pdf, to_parsed_document


def main(argv: list[str]) -> int:
    load_dotenv()
    targets = [Path(p) for arg in argv for p in glob.glob(arg)] or [
        Path(p) for p in sorted(glob.glob("data/samples/demo/*.pdf"))
    ]
    if not targets:
        print("대상 파일이 없습니다. 먼저 py -3 -m scripts.make_demo_package 를 실행하세요.")
        return 1

    print(f"=== 데모 판독 결과 생성 — {len(targets)}건 ===\n")
    for path in targets:
        raw = path.read_bytes()
        pdf = load_pdf(raw, document_id=path.name)
        result = extract_document(
            to_parsed_document(pdf), use_llm=True,
            locator=pdf.locate, page_renderer=pdf.render_page,
        )
        save_result(raw, path.name, result)
        filled = {k: v for k, v in field_map(result).items() if v}
        print(f"  {path.name[:34]:36} {result.doc_type:20} 필드 {len(filled)}개")

    print(f"\n{STORE_DIR} 에 저장했습니다.")
    print("이제 API 키 없는 환경에서도 같은 파일로 시연할 수 있습니다.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
