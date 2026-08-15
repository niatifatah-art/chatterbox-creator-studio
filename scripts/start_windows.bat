@echo off
setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo Chatterbox Creator Studio is not set up yet.
  echo Run scripts\setup_windows.bat first.
  pause
  exit /b 1
)

set PYTHONUTF8=1
".venv\Scripts\python.exe" app.py
if errorlevel 1 (
  echo.
  echo The studio stopped with an error. Keep this window open when asking for help.
  pause
)
