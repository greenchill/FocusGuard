@echo off
REM ===================================================================
REM  FocusGuard optional local setup.
REM  Creates a local .venv next to this file and installs the
REM  dependencies from requirements.txt into it.
REM
REM  NOTE: this re-downloads roughly 1 GB of packages (mediapipe,
REM  ultralytics/torch, opencv). Reusing the existing shared FocusGuard
REM  .venv is much faster and is what run_gui.bat uses by DEFAULT, so
REM  you usually do NOT need this script - just run run_gui.bat.
REM ===================================================================

chcp 65001 >nul
cd /d "%~dp0"

echo Creating a local virtual environment in .venv ...
py -3 -m venv .venv
if errorlevel 1 (
    echo [!] Could not create .venv with "py -3 -m venv". Is Python 3 installed?
    pause
    exit /b 1
)

echo Installing dependencies from requirements.txt (this can take a while) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [!] pip install failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo Done. You can now launch the app with run_gui.bat.
echo (Reminder: the shared FocusGuard .venv is faster if you already have it.)
pause
