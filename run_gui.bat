@echo off
REM ===================================================================
REM  FocusGuard launcher.
REM  Picks the first working Python interpreter, then runs app.py.
REM  Order of preference:
REM    1) a local .venv next to this file (if present)
REM    2) the shared FocusGuard .venv (PyQt6 + cv2 + mediapipe + numpy
REM       + ultralytics + sounddevice already installed there)
REM    3) the Python launcher (py -3)
REM    4) plain python on PATH
REM  Pauses at the end so any error stays visible.
REM ===================================================================

REM UTF-8 console so the English UI/log text renders correctly.
chcp 65001 >nul

REM Run from this script's own folder regardless of where it was launched.
cd /d "%~dp0"

set "PYEXE="

REM 1) Local project virtual environment.
if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
    goto run
)

REM 2) Shared FocusGuard virtual environment (the fast default).
if exist "C:\Users\david\Desktop\FocusGuard\.venv\Scripts\python.exe" (
    set "PYEXE=C:\Users\david\Desktop\FocusGuard\.venv\Scripts\python.exe"
    goto run
)

REM 3) The Windows Python launcher.
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYEXE=py -3"
    goto run
)

REM 4) Last resort: whatever 'python' resolves to on PATH.
set "PYEXE=python"

:run
echo Using Python: %PYEXE%
%PYEXE% app.py %*

echo.
echo FocusGuard exited. Press any key to close this window.
pause >nul
