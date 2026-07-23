"""실물 검증 코퍼스 다운로드 — 하니스(eval_extraction) 재현용.

실물 상품설명서/투자설명서는 저작권·용량 문제로 커밋하지 않고(data/eval는 gitignore),
공개 URL에서 각자 내려받는다. 5개 운용/증권사, ETF·채권·주식·MMF·ELS·DLS, 위험 1~5등급.

실행:
    py -3 -m scripts.fetch_eval_samples
    py -3 -m scripts.eval_extraction --dir data/eval   # 이어서 검증

발표 어필: 실제 KB국민은행 계열(KB자산운용) 상품설명서로 시연 가능(아래 KB 항목).
※ URL은 각 사 공시 페이지 기준이라 시간이 지나면 바뀔 수 있다(실패 시 최신본으로 교체).
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.request import Request, urlopen

OUT = Path("data/eval")

# (파일명, URL, 비고)
SAMPLES = [
    # --- KB 브랜드 (심사 어필용) ---
    ("KB자산_글로벌메타버스_2등급.pdf",
     "http://zeroin.funddoctor.co.kr/cobranding/download/8300_K55223DM9615_2_(31J0)KB%EA%B8%80%EB%A1%9C%EB%B2%8C%EB%A9%94%ED%83%80%EB%B2%84%EC%8A%A4%EA%B2%BD%EC%A0%9C%EC%A6%9D%EA%B6%8C%EC%9E%90(%EC%A3%BC%EC%8B%9D)(H)%ED%88%AC%EC%9E%90%EC%84%A4%EB%AA%85%EC%84%9C-220825(%EA%B0%B1%EC%8B%A0).pdf"),
    ("KB_퇴직연금배당_주식_1등급.pdf",
     "https://securities.miraeasset.com/bbs/download/1997293.pdf?attachmentId=1997293"),
    ("KB_브릭스40_채권혼합_3등급.pdf",
     "https://securities.miraeasset.com/bbs/download/1996868.pdf?attachmentId=1996868"),
    ("KB_국고채30년_채권_3등급.pdf",
     "https://m.funetf.co.kr/upload/FOK/gongsi/R2_K55223DJ2151_20240717.pdf"),
    # --- 삼성 KODEX (등급 1~5) ---
    ("삼성_2차전지_1등급.pdf", "https://m.samsungfund.com/upload/invest/2ETFB1-A.pdf"),
    ("삼성_2등급.pdf", "https://m.samsungfund.com/upload/invest/2ETF57-A.pdf"),
    ("삼성_3등급.pdf", "https://m.samsungfund.com/upload/invest/2ETFM1-A.pdf"),
    ("삼성_4등급.pdf", "https://m.samsungfund.com/upload/invest/2ETF99-A.pdf"),
    ("삼성_MMF_5등급.pdf", "https://m.samsungfund.com/upload/invest/2ETFR3-A.pdf"),
    # --- 미래에셋 (ETF·ELS) ---
    ("미래에셋_반도체_1등급.pdf",
     "https://image.kebhana.com/cont/download/pensionETF/W41A381180_investbrief.pdf"),
    ("미래에셋_ELS4716.pdf",
     "https://securities.miraeasset.com/public/editor/a_ELS4716_invest.pdf"),
    # --- 신한·대우 (ELS·DLS) ---
    ("신한_ELS핵심설명서.pdf", "http://file.shinhansec.com/filedoc/otc/ELS-20220620-012.pdf"),
    ("대우_DLS611.pdf", "https://securities.miraeasset.com/public/editor/D611%5B5%5D.PDF"),
]


def _download(url: str, dest: Path) -> int:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=40) as r:
        data = r.read()
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, url in SAMPLES:
        dest = OUT / name
        try:
            size = _download(url, dest)
            if size < 8000:
                dest.unlink(missing_ok=True)
                print(f"  실패 {name} (너무 작음 {size}B — URL 변경 가능)")
                continue
            print(f"  OK   {name} ({size:,}B)")
            ok += 1
        except Exception as exc:
            print(f"  실패 {name} ({type(exc).__name__}) — 최신 URL로 교체 필요")
    print(f"\n{ok}/{len(SAMPLES)}개 다운로드 → data/eval/")
    print("이어서: py -3 -m scripts.eval_extraction --dir data/eval")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
