@echo off
REM ASCII only. cmd reads .bat files with the OEM codepage, so UTF-8 Korean
REM here breaks parsing. Korean messages are printed by scripts/launch.py.
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Do not use "py -3": the older launcher ranks 3.9 above 3.12 and picks an
REM end-of-life runtime (measured on the dev machine). Probe versions by name.
set "PYEXE="
for %%V in (3.12 3.13 3.11 3.10 3.14) do (
    if not defined PYEXE (
        py -%%V -c "import sys" >nul 2>&1
        if !errorlevel! equ 0 set "PYEXE=py -%%V"
    )
)

if not defined PYEXE (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if !errorlevel! equ 0 set "PYEXE=python"
)

if not defined PYEXE (
    echo.
    echo [ERROR] Python 3.10 or newer was not found.
    echo         Install it from https://www.python.org/downloads/
    echo         and check "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

%PYEXE% "%~dp0scripts\launch.py"

pause
