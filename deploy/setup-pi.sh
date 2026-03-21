#!/usr/bin/env bash
# Set up Lucubrator on a Raspberry Pi (Debian/Bookworm).
# Run as your normal user (not root). Uses sudo where needed.
set -euo pipefail

PROJECT_DIR="/home/$(whoami)/canonbot"
VENV="$PROJECT_DIR/venv"

echo "=== 1. System packages ==="
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git

echo "=== 2. Swap (8GB on SSD) ==="
if [ ! -f /swapfile ]; then
    sudo fallocate -l 8G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "  8GB swap created"
else
    echo "  Swap already exists, skipping"
fi

echo "=== 3. Python venv ==="
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "=== 4. Systemd service ==="
sudo cp "$PROJECT_DIR/deploy/lucubrator.service" /etc/systemd/system/
# Patch User to current user
sudo sed -i "s/^User=.*/User=$(whoami)/" /etc/systemd/system/lucubrator.service
sudo sed -i "s|/home/pi/canonbot|$PROJECT_DIR|g" /etc/systemd/system/lucubrator.service
sudo systemctl daemon-reload
sudo systemctl enable lucubrator
sudo systemctl start lucubrator
echo "  Runner started. Check: journalctl -u lucubrator -f"

echo "=== 5. Cron jobs ==="
chmod +x "$PROJECT_DIR/deploy/daily-review.sh"
chmod +x "$PROJECT_DIR/deploy/rebuild-site.sh"

# Patch paths in cron scripts
sed -i "s|/home/pi/canonbot|$PROJECT_DIR|g" "$PROJECT_DIR/deploy/daily-review.sh"
sed -i "s|/home/pi/canonbot|$PROJECT_DIR|g" "$PROJECT_DIR/deploy/rebuild-site.sh"

# Install cron: daily review at 23:30 UTC, site rebuild at 23:45 UTC
CRON_DAILY="30 23 * * * $PROJECT_DIR/deploy/daily-review.sh >> $PROJECT_DIR/data/logs/daily-review.log 2>&1"
CRON_SITE="45 23 * * * $PROJECT_DIR/deploy/rebuild-site.sh >> $PROJECT_DIR/data/logs/rebuild-site.log 2>&1"

(crontab -l 2>/dev/null | grep -v 'daily-review.sh' | grep -v 'rebuild-site.sh'; echo "$CRON_DAILY"; echo "$CRON_SITE") | crontab -
echo "  Cron installed: daily review 23:30 UTC, site rebuild 23:45 UTC"

echo ""
echo "=== Done ==="
echo "Don't forget to:"
echo "  1. Copy .env to $PROJECT_DIR/.env"
echo "  2. Copy data/interactions.db (or start fresh)"
echo "  3. Copy data/chroma/ (or re-run ingest — takes ~1.5hr)"
echo "  4. Set up git credentials for site push (gh auth or SSH key)"
