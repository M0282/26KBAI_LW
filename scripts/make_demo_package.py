"""발표용 데모 서류 패키지 생성 (재현 가능, 바이너리 미커밋).

실행: py -3 -m scripts.make_demo_package
결과: data/samples/demo/ 아래 4종 PDF 생성 (git 미추적)

의도적으로 심어둔 컴플라이언스 이슈(발표 시연용):
1. 적합성 위반(FIT-001): 고객 '안정형'인데 상품 '1등급(고위험)' → 금소법 17조
2. 설명 확인 결함(ACK-001): 설명 확인서에 고객 확인 '미서명'
3. 날짜 역전(DATE-001): 설명일이 계약일보다 늦음
모든 값은 라벨 형태로 원문에 존재 → 근거 조문·서류 하이라이트가 정확히 동작.
"""
from __future__ import annotations

from pathlib import Path

import fitz

OUT = Path("data/samples/demo")
FONT_PATH = r"C:\Windows\Fonts\malgun.ttf"

DOCS = {
    "01_적합성진단표.pdf": [
        ("금융상품 적합성 진단표", 18),
        ("", 0),
        ("고객명: 김국민", 12),
        ("투자성향: 안정형", 12),
        ("투자목적: 원금 보전 및 예금 수준 안정 수익", 12),
        ("진단일: 2026-07-10", 12),
    ],
    "02_상품설명서.pdf": [
        ("금융투자상품 설명서", 18),
        ("", 0),
        ("상품명: KB 글로벌 하이일드 증권투자신탁", 12),
        ("상품코드: KBGHY-2026", 12),
        ("위험등급: 1등급 (매우 높은 위험)", 12),
        ("원금손실: 본 상품은 원금이 보장되지 않으며 전부 손실될 수 있습니다.", 11),
        ("수수료: 선취판매수수료 1.0%, 운용보수 연 0.7%", 11),
    ],
    "03_가입신청서.pdf": [
        ("금융상품 가입신청서", 18),
        ("", 0),
        ("상품명: KB 글로벌 하이일드 증권투자신탁", 12),
        ("상품코드: KBGHY-2026", 12),
        ("가입금액: 30,000,000원", 12),
        ("계약일: 2026-07-12", 12),
    ],
    "04_설명확인서.pdf": [
        ("상품설명 확인서", 18),
        ("", 0),
        ("상품명: KB 글로벌 하이일드 증권투자신탁", 12),
        ("설명일: 2026-07-15", 12),          # 계약일(07-12)보다 늦음 → DATE-001
        ("설명 담당자: 이판매", 12),
        ("고객 확인: 미서명", 12),            # ACK-001 위반
    ],
}


def _make(path: Path, lines: list[tuple[str, int]]) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="malgun", fontfile=FONT_PATH)
    y = 60
    for text, size in lines:
        if text:
            page.insert_text((60, y), text, fontname="malgun", fontsize=size)
        y += (size + 10) if size else 12
    doc.save(str(path))
    doc.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, lines in DOCS.items():
        _make(OUT / name, lines)
        print(f"생성: {OUT / name}")
    print(f"\n총 {len(DOCS)}종 데모 서류 생성 완료 → 앱에 업로드하거나 아래로 검증:")
    print("  py -3 -m scripts.run_demo")


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    main()
