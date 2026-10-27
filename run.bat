@echo off
REM Chạy bot trên Windows (lần đầu tự tạo môi trường ảo và cài thư viện)
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -U pip
  .venv\Scripts\pip install -r requirements.txt
)
if not exist .env (
  copy .env.example .env
  echo Da tao .env - hay dien khoa API va token roi chay lai.
  pause
  exit /b 1
)
if not exist config\agents.yaml copy config\agents.example.yaml config\agents.yaml
.venv\Scripts\python -m botai %*
