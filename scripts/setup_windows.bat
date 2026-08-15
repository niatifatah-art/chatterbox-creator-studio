@echo off
setlocal
cd /d "%~dp0\.."

echo.
echo === Chatterbox Creator Studio - Windows setup ===
echo.

py -3.11 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 was not found through the Windows py launcher.
  echo Install Python 3.11 from python.org, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python 3.11 virtual environment...
  py -3.11 -m venv .venv
  if errorlevel 1 goto :fail
)

echo Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail

echo Installing Chatterbox Creator Studio dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Setup complete.
echo First generation will download the official Chatterbox Multilingual V3 model from Hugging Face if it is not already cached.
echo Run scripts\start_windows.bat to launch the studio.
pause
exit /b 0

:fail
echo.
echo Setup failed. Scroll up to the first error and include it when asking for help.
pause
exit /b 1
