@echo off
setlocal
cd /d "%~dp0\.."

echo.
echo === Creator Studio - Windows setup ===
echo.

py -3.11 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 was not found through the Windows py launcher.
  echo Install Python 3.11 from python.org, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the local app environment...
  py -3.11 -m venv .venv
  if errorlevel 1 goto :fail
)

echo Updating installer tools...
".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail

echo Installing Creator Studio...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Setup complete.
echo Open the app with scripts\start_windows.bat.
echo Models are managed from inside the app. A model is downloaded only when you install it or choose to use a missing one.
pause
exit /b 0

:fail
echo.
echo Setup failed. Scroll up to the first error and include it when asking for help.
pause
exit /b 1
