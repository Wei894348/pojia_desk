@echo off
chcp 65001 >nul
rem 破甲 · 重试网关（判拒自动重问）
rem :7866 -> anthropic_bridge(:7864)  给 Claude Code 用
rem :7867 -> codex_bridge(:7865)      给 Codex 用
setlocal
cd /d "%~dp0"
set PY=C:\Users\86157\.workbuddy\binaries\python\versions\3.13.12\python.exe
if not exist "%PY%" set PY=python

echo 拉起重试网关 :7866 / :7867 ...
start "pojia-retry-cc"    /min "%PY%" tools\retry_gateway.py --listen 7866 --upstream http://127.0.0.1:7864 --name cc    --retry 2
start "pojia-retry-codex" /min "%PY%" tools\retry_gateway.py --listen 7867 --upstream http://127.0.0.1:7865 --name codex --retry 2
timeout /t 2 >nul
"%PY%" tools\retry_gateway_ctl.py status
echo.
echo 停：python tools\retry_gateway_ctl.py stop
endlocal
