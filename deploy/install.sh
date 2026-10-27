#!/usr/bin/env bash
# Cài Bot AI lên VPS Ubuntu/Debian. Chạy với quyền root:
#   curl -fsSL https://raw.githubusercontent.com/<user>/<repo>/main/deploy/install.sh | bash -s -- https://github.com/<user>/<repo>.git
# hoặc sau khi đã git clone:  sudo bash deploy/install.sh
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR=/opt/bot-ai

echo "==> Cài gói hệ thống (python, ffmpeg, git, font)"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip ffmpeg git fonts-dejavu-core

id botai >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin botai

if [ -n "$REPO_URL" ] && [ ! -d "$APP_DIR/.git" ]; then
  echo "==> Tải mã nguồn từ $REPO_URL"
  git clone "$REPO_URL" "$APP_DIR"
elif [ ! -d "$APP_DIR" ]; then
  echo "==> Chép mã nguồn hiện tại vào $APP_DIR"
  mkdir -p "$APP_DIR"
  cp -r "$(cd "$(dirname "$0")/.." && pwd)"/. "$APP_DIR"/
fi

cd "$APP_DIR"
echo "==> Tạo môi trường Python & cài thư viện"
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt

[ -f .env ] || cp .env.example .env
[ -f config/agents.yaml ] || cp config/agents.example.yaml config/agents.yaml
mkdir -p data
chown -R botai:botai "$APP_DIR"
chmod 600 .env

cp deploy/botai.service /etc/systemd/system/botai.service
systemctl daemon-reload
systemctl enable botai

cat <<MSG

✅ Đã cài xong vào $APP_DIR
Bước tiếp theo:
  1) nano $APP_DIR/.env                 # điền ANTHROPIC_API_KEY, token Telegram, ADMIN_TELEGRAM_IDS
  2) nano $APP_DIR/config/agents.yaml   # (tùy chọn) chỉnh đội trợ lý
  3) sudo -u botai $APP_DIR/.venv/bin/python -m botai check
  4) systemctl start botai && journalctl -u botai -f
Duyệt người dùng mới:  cd $APP_DIR && sudo -u botai .venv/bin/python -m botai approve <MÃ>
MSG
