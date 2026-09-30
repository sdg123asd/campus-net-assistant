@echo off
chcp 65001 >nul 2>nul
title Campus Net Switch
setlocal
set "PS1=%~dp0switch.ps1"
if not exist "%PS1%" (
  echo.
  echo   [ERROR] switch.ps1 not found.
  echo   Please keep all files in the same folder.
  echo.
  pause
  exit /b 1
)
where powershell >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [ERROR] Windows PowerShell not found.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
echo.
pause
