#!/usr/bin/env bash
# Chạy bot trên máy Mac/Linux (lần đầu tự tạo môi trường ảo và cài thư viện)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi
[ -f .env ] || { cp .env.example .env; echo "Đã tạo .env — hãy điền khóa API và token rồi chạy lại."; exit 1; }
[ -f config/agents.yaml ] || cp config/agents.example.yaml config/agents.yaml
exec .venv/bin/python -m botai "$@"
