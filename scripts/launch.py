"""심사·시연용 실행 부트스트랩 — 가상환경 준비부터 앱 기동까지 한 번에.

`run.bat` 이 파이썬만 찾아서 이 스크립트에 넘긴다. 배치 파일에는 ASCII만 두고
한글 안내는 여기서 출력한다 — cmd는 배치 파일을 OEM 코드페이지로 읽어서
UTF-8 한글이 들어가면 파싱 자체가 깨진다(실측).

직접 실행해도 된다:
    py -3.12 scripts/launch.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
MIN_VERSION = (3, 10)


def _say(message: str = "") -> None:
    # 패키지 설치는 몇 분 걸린다. 버퍼에 갇히면 심사위원은 멈춘 줄 안다.
    print(message, flush=True)


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _run(args: list[str], step: str) -> None:
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        _say(f"\n[오류] {step}에 실패했습니다. (종료 코드 {result.returncode})")
        sys.exit(result.returncode)


def main() -> int:
    _say("=" * 60)
    _say(" KB 판매서류 컴플라이언스 검증 Copilot")
    _say("=" * 60)

    if sys.version_info < MIN_VERSION:
        need = ".".join(str(part) for part in MIN_VERSION)
        _say(f"\n[오류] Python {need} 이상이 필요합니다. 현재 {sys.version.split()[0]}")
        _say("       https://www.python.org/downloads/ 에서 설치하세요.")
        return 1

    _say(f"\n[1/4] Python {sys.version.split()[0]}")

    python = _venv_python()
    if python.exists():
        _say("[2/4] 가상환경이 이미 있습니다.")
    else:
        _say("[2/4] 가상환경을 만듭니다. 처음 한 번만 걸립니다.")
        _run([sys.executable, "-m", "venv", str(VENV)], "가상환경 생성")

    _say("[3/4] 필요한 패키지를 확인합니다. 처음에는 몇 분 걸릴 수 있습니다.")
    pip_common = ["-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    _run([str(python), *pip_common, "--upgrade", "pip"], "pip 갱신")
    _run([str(python), *pip_common, "-r", str(ROOT / "requirements.txt")], "패키지 설치")

    if not (ROOT / ".env").exists():
        _say("\n[경고] .env 파일이 없습니다 — 제출본에는 포함돼 있어야 합니다.")
        _say("       키가 없으면 앱이 검증을 중단합니다. 잘못된 판정을 내놓지 않기")
        _say("       위한 의도된 동작입니다.\n")

    _say("[4/4] 앱을 실행합니다. 브라우저가 자동으로 열립니다.")
    _say("      종료하려면 이 창에서 Ctrl+C 를 누르세요.\n")
    completed = subprocess.run(
        [str(python), "-m", "streamlit", "run", str(ROOT / "app" / "main.py")],
        cwd=str(ROOT),
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
