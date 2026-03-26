@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing virtual environment at .venv
  echo Run: python -m venv .venv
  exit /b 1
)

".venv\Scripts\python.exe" ai_with_tools.py
