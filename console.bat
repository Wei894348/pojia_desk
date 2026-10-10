@echo off
rem ==================================================================
rem  console.bat -- unified entry for dsh / codex / workbuddy
rem
rem  Usage:
rem    console.bat                 -> interactive menu
rem    console.bat codex apply     -> jailbreak one target
rem    console.bat wb revert       -> restore one target
rem    console.bat all test        -> verify all deployed artifacts
rem  Actions: apply / revert / status / test / dry
rem  100%% ASCII on purpose (see one-key launcher notes).
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

chcp 65001 >nul

rem this launcher drives console.py (it is the one that takes <target> <action>;
rem 破甲一键通.py -- the engine -- takes --flags, use 一键破甲.bat for that)
set "SCRIPT=%~dp0engine\console.py"

if not exist "%SCRIPT%" (
    echo   [!] engine\console.py not found next to this launcher.
    pause
    exit /b 1
)

"%PYEXE%" "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo   [exit code %RC%]
    pause
)
