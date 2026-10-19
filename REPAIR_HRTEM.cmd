@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1" -Mode repair %*
set "HRTEM_EXIT=%ERRORLEVEL%"
if not "%HRTEM_EXIT%"=="0" (
  echo HRTEM failed. See logs\codex_repair.log
  if "%~1"=="" pause
)
exit /b %HRTEM_EXIT%
