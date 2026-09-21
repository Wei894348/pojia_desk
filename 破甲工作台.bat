@echo off
set "ELECTRON_RUN_AS_NODE="
set "ELECTRON_NO_ATTACH_CONSOLE="
cd /d "%~dp0"
set "ELEC=%~dp0gui\node_modules\electron\dist\electron.exe"
if not exist "%ELEC%" (
  echo.
  echo   [x] Electron runtime not found.
  echo       Run once:  cd gui ^&^& npm install
  echo.
  pause
  exit /b 1
)
start "" "%ELEC%" "%~dp0gui"
exit /b 0
