@echo off
rem ==================================================================
rem  unlock-workbuddy.bat -- pojia for WorkBuddy only (target locked)
rem  ASCII-only on purpose (same reason as the root launcher).
rem  Extra args are appended, e.g.:  unlock-workbuddy.bat --revert
rem  NOTE: fully exit WorkBuddy (incl. tray) and relaunch to take effect.
rem ==================================================================

cd /d "%~dp0"

rem The engine is the only non-ASCII *.py under engine\ and NTFS sorts ASCII
rem before CJK, so the last iteration is it (keeps this file 100%% ASCII).
set "SCRIPT="
for %%F in ("%~dp0..\..\engine\*.py") do set "SCRIPT=%%~fF"

if not defined SCRIPT (
    echo.
    echo   [!] engine script not found under engine\, keep it with this bat
    echo.
    pause
    exit /b 1
)

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
    echo.
    pause
    exit /b 1
)

chcp 65001 >nul
"%PYEXE%" "%SCRIPT%" --apply --force --yes --target wb %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo   [exit code %RC%]
    pause
)
