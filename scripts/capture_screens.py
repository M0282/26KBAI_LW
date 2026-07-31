"""기술설명서용 화면 캡처 — 실제로 앱을 조작해 찍는다.

데모 서류 4종을 실제로 업로드하고, 판정이 끝난 뒤 화면을 찍는다.
합성 이미지가 아니라 앱이 그 순간 그린 결과다.

선행 조건:
    py -3.12 -m pip install playwright && py -3.12 -m playwright install chromium
    py -3.12 -m streamlit run app/main.py --server.port 8501

실행:
    py -3.12 -m scripts.capture_screens
결과: docs/screens/*.png
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path("docs/screens")
APP_URL = "http://localhost:8501"
DEMO_DIR = Path("data/samples/demo")
# 판정이 끝나기까지 기다릴 시간. LLM 판독이 캐시에 있으면 훨씬 빠르다.
SETTLE_MS = 4000


def _wait_for(page, text: str, timeout: int = 180_000) -> bool:
    """화면에 해당 문구가 나타날 때까지 기다린다."""
    try:
        page.get_by_text(text, exact=False).first.wait_for(timeout=timeout)
        return True
    except Exception:
        return False


def main() -> int:
    from playwright.sync_api import sync_playwright

    files = sorted(DEMO_DIR.glob("*.pdf"))
    if not files:
        print(f"{DEMO_DIR} 에 데모 서류가 없습니다. py -3 -m scripts.make_demo_package 먼저 실행하세요.")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 실제 발표 화면과 비슷한 비율. deviceScaleFactor로 선명도를 올린다.
        page = browser.new_context(
            viewport={"width": 1600, "height": 1200}, device_scale_factor=2
        ).new_page()
        print(f"앱 접속: {APP_URL}")
        page.goto(APP_URL, wait_until="networkidle", timeout=60_000)

        if not _wait_for(page, "판매서류 업로드", timeout=60_000):
            print("업로드 화면을 찾지 못했습니다. 앱이 떠 있는지 확인하세요.")
            browser.close()
            return 1

        print(f"데모 서류 {len(files)}종 업로드 중…")
        page.set_input_files("input[type=file]", [str(f.resolve()) for f in files])

        # 판정이 끝나면 종합 결론 배지가 뜬다.
        print("판정 대기 중… (LLM 판독이 필요하면 수 분 걸릴 수 있음)")
        done = _wait_for(page, "판매 진행 부적합") or _wait_for(page, "검사 항목")
        if not done:
            print("판정 결과를 확인하지 못했습니다.")
            browser.close()
            return 1
        page.wait_for_timeout(SETTLE_MS)

        shots = [
            ("01_판정요약.png", "2. 패키지 교차 검증", "first"),
            # 첫 번째 검사 항목의 조문은 교차 검증 제목 바로 아래라 같은 화면이 된다.
            # 마지막 항목을 잡아야 별개의 컷이 나온다(실측: 01과 해시 동일).
            # 조문 문구가 아니라 항목 제목을 앵커로 삼는다 — 조문에 맞추면 '최우선 근거'
            # 줄이 화면 위로 잘린다. 사이드바 규칙 목록에도 같은 ID가 있어 last를 쓴다.
            # 판정 근거·원문 근거 보기가 규칙 제목과 조문 사이에 들어오면서
            # 규칙 ID를 앵커로 삼으면 정작 조문 하이라이트가 화면 아래로 밀린다.
            # 이 컷의 주제는 조문이므로 '근거 조문' 머리글을 앵커로 삼는다.
            # '근거 조문'만으로는 5번 섹션 설명문에도 걸려 맨 아래로 스크롤된다(실측).
            # 근거 조문 머리글에만 있는 괄호 문구를 앵커로 쓴다.
            ("02_근거조문.png", "판정과 직접 연결되는 부분", "last"),
            ("03_정량지표.png", "3. 정량 지표", "first"),
            ("04_하이라이트.png", "4. 서류 원문 하이라이트", "first"),
        ]
        for name, anchor, which in shots:
            matches = page.get_by_text(anchor, exact=False)
            target = matches.last if which == "last" else matches.first
            try:
                # 앵커를 화면 맨 위로 올린다. scroll_into_view_if_needed 는 이미 보이면
                # 스크롤하지 않아 여러 컷이 같은 화면으로 저장된다(실측: 3장이 동일 해시).
                target.evaluate("el => el.scrollIntoView({block: 'start'})")
                page.wait_for_timeout(1500)
            except Exception:
                print(f"  · {anchor} 위치를 찾지 못해 현재 화면으로 대체")
            page.screenshot(path=str(OUT / name))
            print(f"  저장: {OUT / name}")

        # Streamlit은 body가 아니라 내부 컨테이너가 스크롤된다. full_page=True 로는
        # 페이지 높이가 늘지 않아 마지막 뷰포트와 같은 그림이 나온다(실측: 해시 동일).
        # 콘텐츠 블록 자체를 찍어야 전체 높이가 담긴다.
        # ── 판매 채널·고객 조건 증거 컷 ──────────────────────
        # 대면·비대면과 고령투자자, 설명 담당자는 판매 건 요약 표에 함께 나온다.
        # 그 표는 판매 건이 둘 이상일 때만 뜨므로 같은 서류로 칸을 하나 더 만든다.
        try:
            page.get_by_role("button", name="판매 건 추가").click()
            page.wait_for_timeout(3000)
            uploaders = page.locator("input[type=file]")
            uploaders.nth(1).set_input_files([str(f.resolve()) for f in files])
            page.wait_for_timeout(2500)
            # 두 번째 칸만 비대면으로 표시해 채널 차이가 표에 드러나게 한다.
            nonface = page.get_by_text("비대면(모바일·인터넷) 가입입니다", exact=False)
            if nonface.count() >= 2:
                nonface.nth(1).click()
            _wait_for(page, "판매 건 요약", timeout=300_000)
            page.wait_for_timeout(6000)

            checks = page.get_by_text("고령투자자(만 65세 이상)", exact=False).first
            checks.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "05_채널체크.png"))
            print(f"  저장: {OUT / '05_채널체크.png'}")

            table = page.locator('[data-testid="stDataFrame"]').first
            table.scroll_into_view_if_needed()
            page.wait_for_timeout(1200)
            table.screenshot(path=str(OUT / "06_판매건요약.png"))
            print(f"  저장: {OUT / '06_판매건요약.png'} (채널·고령·담당자)")
        except Exception as exc:
            print(f"  · 채널 증거 컷을 찍지 못했습니다: {type(exc).__name__}")

        # 정량 지표는 숫자만 크게 보이면 되므로 위쪽 지표 줄만 잘라 따로 둔다.
        # 같은 실행에서 나온 그림이라 덱에 적은 수치와 화면 값이 어긋나지 않는다.
        try:
            from PIL import Image as _Image

            _Image.open(OUT / "03_정량지표.png").crop((700, 90, 3190, 500)).save(
                OUT / "07_정량지표스트립.png"
            )
            print(f"  저장: {OUT / '07_정량지표스트립.png'} (지표 줄만)")
        except Exception as exc:
            print(f"  · 지표 스트립을 만들지 못했습니다: {type(exc).__name__}")

        whole = OUT / "00_전체.png"
        block = page.locator('[data-testid="stMainBlockContainer"]').first
        try:
            block.screenshot(path=str(whole))
        except Exception:
            print("  · 콘텐츠 블록을 찾지 못해 전체 페이지 캡처로 대체")
            page.screenshot(path=str(whole), full_page=True)
        print(f"  저장: {whole} (전체 화면)")
        browser.close()

    print(f"\n캡처 완료 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
