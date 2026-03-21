# Deploying Lucubrator to a Raspberry Pi

## Prerequisites
- Raspberry Pi 4/5 (4GB+ RAM) with Debian 13 or Raspberry Pi OS
- SSD recommended (swap + storage); 8GB swap configured
- SSH access, Wi-Fi or ethernet

## Quick start

```bash
# 1. Copy the project to the Pi
rsync -avz --exclude='venv' --exclude='data/chroma' --exclude='data/site_build' \
  --exclude='data/lucubrator_repo' --exclude='.git' \
  ~/Desktop/canonbot/ pi@lucubrator.local:~/canonbot/

# 2. Copy data separately (large)
rsync -avz ~/Desktop/canonbot/data/interactions.db pi@lucubrator.local:~/canonbot/data/
rsync -avz ~/Desktop/canonbot/data/chroma/ pi@lucubrator.local:~/canonbot/data/chroma/

# 3. Copy your .env
scp ~/Desktop/canonbot/.env pi@lucubrator.local:~/canonbot/.env

# 4. SSH in and run setup
ssh pi@lucubrator.local
cd ~/canonbot
mkdir -p data/logs
bash deploy/setup-pi.sh
```

## What it sets up

- **systemd service** (`lucubrator`): runs the timeline poller, auto-restarts on failure
- **Daily cron** (23:30 UTC): Opus review — selects best entries, writes reflection
- **Site cron** (23:45 UTC): rebuilds static site, pushes to GitHub Pages

## After setup

Unbuffered logging (so journalctl shows output in real time):
```bash
sudo systemctl edit lucubrator
# Add between the comment lines:
#   [Service]
#   Environment=PYTHONUNBUFFERED=1
sudo systemctl restart lucubrator
```

Disable Wi-Fi power save (prevents dropped connections):
```bash
sudo tee /etc/NetworkManager/conf.d/wifi-powersave-off.conf << 'EOF'
[connection]
wifi.powersave = 2
EOF
sudo systemctl restart NetworkManager
```

Git credentials for site push (SSH key):
```bash
ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub
# Add to GitHub > Settings > SSH Keys
ssh -T git@github.com  # test
```

## Deploying code changes

From the Mac:
```bash
rsync -avz --exclude='venv' --exclude='data/chroma' --exclude='data/site_build' \
  --exclude='data/lucubrator_repo' --exclude='.git' \
  ~/Desktop/canonbot/ pi@lucubrator.local:~/canonbot/
ssh pi@lucubrator.local 'sudo systemctl restart lucubrator'
```

## Management

```bash
# Check runner status
sudo systemctl status lucubrator
journalctl -u lucubrator -f

# Restart after code changes
sudo systemctl restart lucubrator

# Check cron logs
tail -f ~/canonbot/data/logs/daily-review.log
tail -f ~/canonbot/data/logs/rebuild-site.log

# Manual daily review
bash ~/canonbot/deploy/daily-review.sh

# Manual site rebuild
bash ~/canonbot/deploy/rebuild-site.sh
```

## Gotchas

- **Python stdout buffering**: systemd captures stdout but Python buffers it. Set `PYTHONUNBUFFERED=1` via `systemctl edit` (see above) or logs appear empty.
- **Stale timeline cursor**: if the runner has been down for days, delete `data/timeline_cursor.txt` and restart so it picks up fresh posts.
- **ChromaDB ONNX GPU warning**: harmless on Pi — it looks for a GPU, doesn't find one, falls back to CPU.
- **ChromaDB OOM**: 4GB RAM is tight. 8GB swap on SSD makes it work. Don't run other heavy processes.
- **Wi-Fi power save**: Pi's wireless chip sleeps by default, causing dropped connections. Disable it (see above).
- **Ghostty SSH terminal**: Pi may not have Ghostty's terminfo. Set `export TERM=xterm-256color` in the SSH session if keys behave oddly.
