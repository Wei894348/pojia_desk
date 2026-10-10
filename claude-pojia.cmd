@echo off
REM ============================================================
REM  claude-pojia : Claude Code writing-mode launcher
REM
REM  Stack: binary patch (tools/cc_binary_pojia_patch.py)
REM         + --system-prompt-file full version (content clauses)
REM         + --append-system-prompt-file content\persona.md
REM  Measured : 3/3 writes on the bare prompt.
REM
REM  NOTE: --system-prompt-file replaces the default system prompt,
REM        so the built-in tool guidance is gone. Use this launcher
REM        for writing/content work; use plain "claude" for coding.
REM  Undo : python tools/cc_binary_pojia_patch.py --restore
REM ============================================================
setlocal
set "POJIA=%USERPROFILE%\.claude\pojia-system.md"
REM persona lives in content\ (repo-local, resolved relative to this file)
set "PERSONA=%~dp0content\persona.md"

if not exist "%POJIA%" (
  echo [!] missing %POJIA%
  echo     run: python engine\破甲一键通.py --target claude --apply --force --yes
  exit /b 1
)
if not exist "%PERSONA%" (
  echo [!] missing %PERSONA%
  exit /b 1
)

"%APPDATA%\npm\claude.CMD" --system-prompt-file "%POJIA%" --append-system-prompt-file "%PERSONA%" %*
endlocal
