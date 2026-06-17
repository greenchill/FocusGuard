@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Build a standalone Windows app (onedir) with PyInstaller.
REM Prefer this project's own .venv; fall back to the shared FocusGuard .venv, then PATH.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\Users\david\Desktop\FocusGuard\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Building FocusGuard.exe (this takes several minutes and is large)...
"%PY%" -m PyInstaller --noconfirm focusguard.spec
echo.
echo Done. Run:  dist\FocusGuard\FocusGuard.exe
pause
