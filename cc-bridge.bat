@echo off
rem ==================================================================
rem  cc-bridge.bat -- Claude Code request-side bridge
rem
rem  Sits between Claude Code and the wb2api bridge:
rem     Claude Code -> 127.0.0.1:7865 (this) -> 127.0.0.1:7864 -> upstream
rem
rem  What it does:
rem     1. normalizes trigger words in the LAST user message
rem        (table: content\cc-synonyms.json)
rem     2. retries when the model replies with a refusal, until it
rem        actually writes the deliverable (--retry 4)
rem
rem  Enable: in %USERPROFILE%\.claude\settings.json set
rem      "ANTHROPIC_BASE_URL": "http://127.0.0.1:7865"
rem  Keep this window open while you use Claude Code.
rem
rem  This file is deliberately 100%% ASCII (see console.bat notes).
rem ==================================================================

cd /d "%~dp0"

set "PYEXE="
where python >nul 2>&1
if %errorlevel%==0 set "PYEXE=python"

if not defined PYEXE (
    where py >nul 2>&1
    if %errorlevel%==0 set "PYEXE=py"
)

if not defined PYEXE (
    for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
        if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
    )
)

if not defined PYEXE (
    echo.
    echo   [!] Python not found.
    pause
    exit /b 1
)

if not exist "%~dp0tools\cc_synonym_bridge.py" (
    echo   [!] tools\cc_synonym_bridge.py not found next to this launcher.
    pause
    exit /b 1
)

chcp 65001 >nul
"%PYEXE%" "%~dp0tools\cc_synonym_bridge.py" --listen 7865 --upstream http://127.0.0.1:7864 --retry 4
echo.
echo   [bridge stopped]
pause
