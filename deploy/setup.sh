#!/bin/bash
# Relay one-shot Oracle Cloud ARM setup
# Run as ubuntu: bash deploy/setup.sh

set -euo pipefail

REPO_DIR="$HOME/relay"

echo "=== Relay deploy setup ==="

# System packages
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3.11-venv python3-pip nginx git

# Clone or pull repo
if [ -d "$REPO_DIR" ]; then
  echo "Pulling latest..."
  git -C "$REPO_DIR" pull --ff-only
else
  echo "Cloning..."
  git clone https://github.com/AryanG01/relay.git "$REPO_DIR"
fi

cd "$REPO_DIR"

# Python venv
if [ ! -d venv ]; then
  python3.11 -m venv venv
fi
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Ensure .env exists
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  Edit .env and add ANTHROPIC_API_KEY and SECRET_KEY, then re-run."
  exit 1
fi

# DB setup
python3 scripts/setup.py

# Build frontend
if command -v node &> /dev/null; then
  cd frontend && npm ci --silent && npm run build && cd ..
else
  echo "⚠️  node not found — skipping frontend build"
fi

# Install systemd service
sudo cp deploy/relay.service /etc/systemd/system/relay.service
sudo systemctl daemon-reload
sudo systemctl enable relay
sudo systemctl restart relay

# Nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/relay
sudo ln -sf /etc/nginx/sites-available/relay /etc/nginx/sites-enabled/relay
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "✅  Relay deployed!"
echo "   API:       http://$(curl -s ifconfig.me):80/api/health"
echo "   Dashboard: http://$(curl -s ifconfig.me):80"
echo "   Logs:      sudo journalctl -u relay -f"
