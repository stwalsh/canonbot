"""Source-agnostic runner: multiplexes sources -> engine -> output.

Usage:
    ./venv/bin/python -m src.runner              # dry run with configured sources
    ./venv/bin/python -m src.runner --live        # live mode (post to Bluesky)
"""

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv

from src.bluesky.client import BlueskyClient
from src.engine import Engine
from src.sources import SourceMode, SeedAccumulator, build_sources, multiplex
from src.store import Store

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"


def _setup_log() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("canonbot")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_DIR / "canonbot.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def _load_config() -> dict:
    with open("config/config.yaml") as f:
        return yaml.safe_load(f) or {}


def _print_result(item, result, dry_run):
    """Pretty-print a brain result to stdout."""
    if result.get("rate_limited"):
        return

    print(f"\n{'='*60}")
    print(f"  POST: {item.text[:120]}")
    print(f"  FROM: {item.author}")
    print(f"  SOURCE: {item.source_name}")

    triage = result.get("triage") or {}
    print(f"  TRIAGE: {'ENGAGE' if triage.get('engage') else 'SKIP'} — {triage.get('reason', '')}")

    if not triage.get("engage"):
        return

    comp = result.get("composition") or {}
    decision = comp.get("decision", "skip")
    mode = comp.get("mode", "?")
    print(f"  COMPOSITION: {decision} ({mode})")

    if decision == "post":
        for i, text in enumerate(comp.get("posts", []), 1):
            print(f"  POST {i}: {text}")
        pu = comp.get("passage_used")
        if pu:
            print(f"  PASSAGE: {pu.get('poet')} — \"{pu.get('poem_title')}\" [{pu.get('chunk_id')}]")
    else:
        print(f"  SKIP REASON: {comp.get('skip_reason', '?')}")

    tag = "DRY RUN" if dry_run else "LIVE"
    print(f"  [{tag}] interaction_id={result.get('interaction_id')}")


def _print_self_result(result):
    """Pretty-print a self-generated result to stdout."""
    comp = result.get("composition") or {}
    if comp.get("decision") != "post":
        print(f"\n  SELF [{result.get('mode', '?')}]: skipped — {comp.get('skip_reason', '?')}")
        return

    mode = result.get("mode", "?")
    print(f"\n{'='*60}")
    print(f"  SELF [{mode.upper()}]: {result.get('search_reason', '')[:120]}")
    print(f"  COMPOSITION: {comp.get('decision')} ({comp.get('mode', '?')})")
    for i, text in enumerate(comp.get("posts", []), 1):
        print(f"  POST {i}: {text}")
    pu = comp.get("passage_used")
    if pu:
        print(f"  PASSAGE: {pu.get('poet')} — \"{pu.get('poem_title')}\" [{pu.get('chunk_id', '')}]")
    print(f"  [DRY RUN] interaction_id={result.get('interaction_id')}")


def _log_result(logger, item, result, dry_run):
    """Write a human-readable log entry."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    tag = "DRY" if dry_run else "LIVE"
    lines = [
        "",
        f"--- [{now}] [{tag}] [{item.source_name}] ---",
        f"Stimulus: {item.text[:200]}",
        f"Author: {item.author}",
    ]

    if result.get("rate_limited"):
        lines.append(f"Result: RATE LIMITED — {result.get('reason')}")
    else:
        triage = result.get("triage") or {}
        engage = triage.get("engage")
        lines.append(f"Triage: {'ENGAGE' if engage else 'SKIP'} — {triage.get('reason', '')}")

        if engage:
            comp = result.get("composition") or {}
            decision = comp.get("decision", "skip")
            lines.append(f"Composition: {decision} ({comp.get('mode', '?')})")
            if decision == "post":
                for i, text in enumerate(comp.get("posts", []), 1):
                    lines.append(f"  Post {i}: {text}")
            else:
                lines.append(f"  Skip reason: {comp.get('skip_reason', '?')}")

    logger.info("\n".join(lines))


async def run(live: bool = False):
    load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    cfg = _load_config()
    dry_run = not live

    logger = _setup_log()
    mode_label = "LIVE" if live else "DRY RUN"
    print(f"  Lucubrator — {mode_label} mode (unified sources)")
    logger.info(f"\n{'='*60}\nLucubrator started — {mode_label} mode (unified sources)\n{'='*60}")

    client = anthropic.Anthropic()
    store = Store()
    engine = Engine(client=client, store=store)

    # Build Bluesky client for live posting
    bsky_client = None
    if live:
        handle = os.environ.get("BSKY_HANDLE")
        password = os.environ.get("BSKY_PASSWORD")
        if not handle or not password:
            print("ERROR: BSKY_HANDLE and BSKY_PASSWORD must be set for live mode.")
            sys.exit(1)
        bsky_client = BlueskyClient()
        bsky_client.login(handle, password)

    # Build sources from config
    source_configs = cfg.get("sources")
    if not source_configs:
        # Backward compat: if no sources block, use timeline
        print("  [sources] No 'sources' config found, falling back to bluesky_timeline")
        source_configs = [{"type": "bluesky_timeline", "mode": "triage"}]

        # Also add legacy seed sources if configured
        legacy_seeds = cfg.get("seeds") or {}
        if legacy_seeds.get("stichomythia_feed"):
            source_configs.append({
                "type": "feed_file",
                "path": legacy_seeds["stichomythia_feed"],
                "mode": "seed",
            })
        if legacy_seeds.get("stimuli_dir"):
            source_configs.append({
                "type": "stimuli_dir",
                "path": legacy_seeds["stimuli_dir"],
                "mode": "seed",
            })

    sources = build_sources(source_configs)
    if not sources:
        print("ERROR: No sources configured.")
        sys.exit(1)

    seeds = SeedAccumulator()

    # Self-generation config
    self_gen_cfg = cfg.get("self_generation") or {}
    quiet_seconds = self_gen_cfg.get("quiet_seconds", 2700)
    self_gen_modes = self_gen_cfg.get("modes", ["contemplate", "contemplate", "compare"])

    last_composition_time = time.time()

    print(f"  Self-gen triggers after {quiet_seconds}s quiet")
    print(f"  Starting source multiplexer...")

    async for mux_item in multiplex(sources):
        item = mux_item.item

        # End-of-cycle sentinel
        if item is None:
            # Only check self-gen on triage source cycles
            if mux_item.source.mode == SourceMode.TRIAGE:
                elapsed = time.time() - last_composition_time
                if elapsed >= quiet_seconds:
                    mode = random.choice(self_gen_modes)
                    print(f"\n  [self] {elapsed:.0f}s without composition — generating ({mode})...")
                    try:
                        sg_result = await asyncio.wait_for(
                            asyncio.to_thread(
                                engine.self_generate,
                                mode=mode,
                                seeds=seeds.as_context_string(),
                                dry_run=dry_run,
                            ),
                            timeout=180,
                        )
                        _print_self_result(sg_result)
                        sg_comp = sg_result.get("composition") or {}
                        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                        tag = "DRY" if dry_run else "LIVE"
                        logger.info(
                            f"\n--- [{now}] [{tag}] [SELF {mode.upper()}] ---\n"
                            f"Reason: {sg_result.get('search_reason', '')}\n"
                            f"Decision: {sg_comp.get('decision', 'skip')}\n"
                            + (("\n".join(f"Post: {p}" for p in sg_comp.get("posts", []))) if sg_comp.get("posts") else "")
                        )
                        if sg_comp.get("decision") == "post":
                            last_composition_time = time.time()
                            await asyncio.sleep(65)
                    except asyncio.TimeoutError:
                        print("  [self] TIMEOUT on self-generation")
                    except Exception as e:
                        print(f"  [self] ERROR: {e}")
            continue

        # Seed item: accumulate
        if item.mode == SourceMode.SEED:
            seeds.update(item)
            print(f"  [seed] Updated from {item.source_name}")
            continue

        # Triage item: process through engine
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    engine.process,
                    stimulus=item.text,
                    source=item.source_name,
                    stimulus_uri=item.uri,
                    stimulus_author=item.author,
                    seeds=seeds.as_context_string(),
                    dry_run=dry_run,
                ),
                timeout=120,
            )
        except asyncio.TimeoutError:
            print(f"\n  TIMEOUT processing: {item.text[:80]}")
            logger.info(f"\n--- TIMEOUT ---\nStimulus: {item.text[:200]}")
            continue
        except Exception as e:
            print(f"\n  ERROR processing: {e}")
            logger.info(f"\n--- ERROR ---\nStimulus: {item.text[:200]}\nError: {e}")
            continue

        _print_result(item, result, dry_run)
        _log_result(logger, item, result, dry_run)

        # Track composition time
        if not result.get("rate_limited"):
            comp = result.get("composition") or {}
            if comp.get("decision") == "post":
                last_composition_time = time.time()
                await asyncio.sleep(65)

        # If live and composition says post, post to Bluesky
        if live and bsky_client and not result.get("rate_limited"):
            comp = result.get("composition") or {}
            if comp.get("decision") == "post" and comp.get("posts"):
                try:
                    reply_to = None
                    if item.uri:
                        resolved = bsky_client.resolve_post(item.uri)
                        reply_to = {"uri": resolved["uri"], "cid": resolved["cid"]}
                    posted = bsky_client.send_thread(comp["posts"], reply_to=reply_to)
                    uris = [p["uri"] for p in posted]
                    print(f"  POSTED: {uris}")
                    store._conn.execute(
                        "UPDATE interactions SET response_uris = ? WHERE id = ?",
                        (json.dumps(uris), result["interaction_id"]),
                    )
                    store._conn.commit()
                except Exception as e:
                    print(f"  ERROR posting: {e}")


def main():
    parser = argparse.ArgumentParser(description="Lucubrator — unified source runner")
    parser.add_argument("--live", action="store_true", help="Actually post (default: dry run)")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        print("\n  Shutting down...")
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(run(live=args.live))
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
