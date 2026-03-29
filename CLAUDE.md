# Lucubrator — Project Instructions

## Quick commands

When the user says any of these, do the corresponding action without asking for clarification:

- **"check the pi"** / **"how's the pi"** — SSH to Pi, show: systemctl status (5 lines), last 15 journal lines, memory (free -m), swap, CPU temp
- **"deploy"** — rsync (excluding venv/data/corpus/__pycache__/.env), restart lucubrator service, verify startup logs
- **"how's he doing"** — get latest reflection (summary + self_notes), plus last 5 interactions (id, timestamp, source, composition_decision)
- **"check stimuli"** — list stimuli folder locally and on Pi, show any that haven't synced
- **"check the site"** — fetch the site index page and summarise what's showing
- **"corpus stats"** — query ChromaDB for total count and top 10 poets by chunk count

## Pi access

- SSH: `ssh -i ~/.ssh/id_pi pi@lucubrator.local`
- Always `cd /home/pi/canonbot` before running Python on Pi
- Use `./venv/bin/python` on Pi (no system python, no sqlite3 binary)
- Service: `sudo systemctl restart lucubrator`
- Logs: `journalctl -u lucubrator --no-pager`

## Rsync command

```
rsync -avz --exclude='venv/' --exclude='data/' --exclude='node_modules/' --exclude='__pycache__/' --exclude='.env' --exclude='corpus/' -e "ssh -i ~/.ssh/id_pi" /Users/swalsh/Desktop/canonbot/ pi@lucubrator.local:/home/pi/canonbot/
```

## Stimuli rsync (just the folder)

```
rsync -avz -e "ssh -i ~/.ssh/id_pi" /Users/swalsh/Desktop/canonbot/config/stimuli/ pi@lucubrator.local:/home/pi/canonbot/config/stimuli/
```

## Key files

- Config: `config/config.yaml` (sources, engine limits, self-gen modes)
- Prompts: `config/prompts/` (soul, system, triage, reflect, contemplate, compare, engage, revise)
- Oblique Strategies: `config/oblique_strategies.md` (88 cards, editable markdown)
- Runner: `src/runner.py` (unified source runner)
- Engine: `src/engine.py` (process, self_generate, engage_stimulus, run_daily_reflection)
- Brain: `src/brain.py` (compose, contemplate, compare, engage, revise_entry, daily_review)
- Sources: `src/sources/` (bluesky_timeline, rss, feed_file, stimuli_dir, multiplexer, seeds)
- Site: `scripts/build_site.py` + `scripts/templates/site/`
- Clipper: `tools/lucubrator-clipper/` (extension + drop server + macOS launch agent)
  - Text clips: Chrome extension → POST /drop (JSON)
  - Photos: iOS Shortcuts → POST /drop-photo (raw JPEG body, via immram.local:3847)
  - Oblique Strategies: `config/oblique_strategies.md` (88 cards, one drawn per composition)

## Commit style

Short summary line describing what changed and why. Multi-line body for larger changes. Always include Co-Authored-By trailer.
