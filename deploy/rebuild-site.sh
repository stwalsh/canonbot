#!/usr/bin/env bash
# Rebuild the static site and push to GitHub Pages. Intended for cron.
set -euo pipefail

cd /home/pi/canonbot
set -a; source .env; set +a

./venv/bin/python scripts/build_site.py
