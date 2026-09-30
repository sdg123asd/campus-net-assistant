@echo off
chcp 65001 >nul 2>nul
title Campus Net Console
setlocal EnableExtensions
cd /d "%~dp0"

set "APP=%~dp0campus_console.py"
if not exist "%APP%" (
  echo.
  echo   [ERROR] campus_console.py not found.
  echo   Please keep all files in the same folder.
  echo.
  pause
  exit /b 1
)

rem ---- locate a real Python (prefer pythonw.exe = no console window) ----
set "PY="
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
if not defined PY if exist "%ProgramFiles%\Python313\pythonw.exe" set "PY=%ProgramFiles%\Python313\pythonw.exe"
if not defined PY if exist "%ProgramFiles%\Python312\pythonw.exe" set "PY=%ProgramFiles%\Python312\pythonw.exe"
if not defined PY if exist "C:\Python313\pythonw.exe" set "PY=C:\Python313\pythonw.exe"
if not defined PY if exist "C:\Python312\pythonw.exe" set "PY=C:\Python312\pythonw.exe"
if not defined PY for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PY set "PY=%%I"

if not defined PY (
  echo.
  echo   [ERROR] Python not found on this computer.
  echo.
  echo   Please install Python 3.10 or newer:
  echo     https://www.python.org/downloads/
  echo   During setup, tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

start "CampusNetConsole" "%PY%" "%APP%" %*
exit /b 0
