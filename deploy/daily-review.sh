#!/usr/bin/env bash
# Run the Opus daily review. Intended for cron.
# Selects best entries, writes reflection, stores self_notes.
set -euo pipefail

cd /home/pi/canonbot
set -a; source .env; set +a

./venv/bin/python -c "
from src.engine import Engine
e = Engine()
result = e.run_daily_reflection()
if result:
    selected = result.get('selected_ids', [])
    print(f'Daily review: selected {len(selected)} entries')
else:
    print('No posts today — skipped')
e.store.close()
"
