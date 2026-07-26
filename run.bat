@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [.venv] 가 없습니다. 먼저 가상환경을 만드세요:
  echo   python -m venv .venv
  echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
".\.venv\Scripts\python.exe" -m app %*
