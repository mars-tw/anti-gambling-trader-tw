@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m core.ui %*
  exit /b !errorlevel!
)

where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -m core.ui %*
  exit /b !errorlevel!
)

where python >nul 2>nul
if %errorlevel% equ 0 (
  python -m core.ui %*
  exit /b !errorlevel!
)

echo Python 3.10 or newer was not found.
echo Install Python, then run: pip install -e .
exit /b 1
