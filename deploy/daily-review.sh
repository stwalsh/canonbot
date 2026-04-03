#!/usr/bin/env bash
# Run the Opus daily review. Intended for cron.
# Selects best entries, writes reflection, runs editorial revision.
set -euo pipefail

cd /home/pi/canonbot
set -a; source .env; set +a

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — Starting daily review"

./venv/bin/python -u -c "
import traceback
from src.engine import Engine

try:
    e = Engine()
    result = e.run_daily_reflection()
    if result:
        selected = result.get('selected_ids', [])
        publish = [s for s in selected if isinstance(s, dict) and s.get('tier') == 'publish']
        notebook = [s for s in selected if isinstance(s, dict) and s.get('tier') == 'notebook']
        non_dict = sum(1 for s in selected if not isinstance(s, dict))
        print(f'Daily review: selected {len(selected)} entries ({len(publish)} publish, {len(notebook)} notebook, {non_dict} non-dict)')
        if result.get('self_notes'):
            print(f'Self-notes: {result[\"self_notes\"][:100]}...')
        else:
            print('WARNING: self_notes is empty')
    else:
        print('No posts today — skipped')
    e.store.close()
except Exception:
    print('ERROR in daily review:')
    traceback.print_exc()
    raise
"

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — Daily review complete"
