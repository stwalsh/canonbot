"""Main entry point: firehose/timeline -> engine -> client (or dry-run log).

Usage:
    ./venv/bin/python -m src.bluesky.runner              # firehose dry run
    ./venv/bin/python -m src.bluesky.runner --live        # firehose live
    ./venv/bin/python -m src.bluesky.runner --timeline    # timeline dry run (blog-only)
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.bluesky.client import BlueskyClient
from src.bluesky.firehose import consume as firehose_consume
from src.bluesky.timeline import consume as timeline_consume
from src.engine import Engine
from src.store import Store

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs"


def _setup_log() -> logging.Logger:
    """Set up file logger at data/logs/canonbot.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("canonbot")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_DIR / "canonbot.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def _log_interaction(logger: logging.Logger, post: dict, result: dict, dry_run: bool):
    """Write a human-readable log entry."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    tag = "DRY" if dry_run else "LIVE"
    lines = [
        "",
        f"--- [{now}] [{tag}] ---",
        f"Stimulus: {post['text'][:200]}",
        f"Author: {post['author_did']}",
        f"Keywords: {', '.join(post['matched_keywords'])}",
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
                pu = comp.get("passage_used")
                if pu:
                    lines.append(f"  Passage: {pu.get('poet')} — \"{pu.get('poem_title')}\"")
            else:
                lines.append(f"  Skip reason: {comp.get('skip_reason', '?')}")

            tokens_in = result.get("tokens_in", 0)
            tokens_out = result.get("tokens_out", 0)
            if tokens_in or tokens_out:
                lines.append(f"  Tokens: {tokens_in} in / {tokens_out} out")

    logger.info("\n".join(lines))


def _print_result(post: dict, result: dict, dry_run: bool):
    """Pretty-print a brain result to stdout."""
    # Don't flood stdout with rate-limited skips
    if result.get("rate_limited"):
        return

    keywords = ", ".join(post["matched_keywords"])
    print(f"\n{'='*60}")
    print(f"  POST: {post['text'][:120]}")
    print(f"  FROM: {post['author_did']}")
    print(f"  KEYWORDS: {keywords}")
    print(f"  URI: {post['post_uri']}")

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


def _print_self_result(result: dict):
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


async def run(live: bool = False, timeline: bool = False):
    load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    # Timeline mode is always dry-run (blog-only)
    if timeline:
        dry_run = True
        source = "bluesky_timeline"
    else:
        dry_run = not live
        source = "bluesky_firehose"

    logger = _setup_log()
    mode_label = "TIMELINE (blog-only)" if timeline else ("LIVE" if live else "DRY RUN")
    print(f"  Lucubrator — {mode_label} mode")
    logger.info(f"\n{'='*60}\nLucubrator started — {mode_label} mode\n{'='*60}")

    client = anthropic.Anthropic()
    store = Store()
    engine = Engine(client=client, store=store)

    bsky_client = None
    if live and not timeline:
        handle = os.environ.get("BSKY_HANDLE")
        password = os.environ.get("BSKY_PASSWORD")
        if not handle or not password:
            print("ERROR: BSKY_HANDLE and BSKY_PASSWORD must be set for live mode.")
            sys.exit(1)
        bsky_client = BlueskyClient()
        bsky_client.login(handle, password)

    if timeline:
        print("  Starting timeline poller...")
        consumer = timeline_consume()
    else:
        print("  Starting firehose consumer...")
        consumer = firehose_consume()

    # Self-generation state
    import random
    _polls_without_composition = 0
    _self_gen_modes = ["contemplate", "contemplate", "compare"]  # 2:1 ratio
    _SELF_GEN_INTERVAL = 3  # trigger after N polls with no compositions

    _composed_this_cycle = False

    async for post in consumer:
        # End-of-cycle sentinel from timeline consumer
        if post.get("_end_of_cycle"):
            if timeline and not _composed_this_cycle:
                _polls_without_composition += 1
                if _polls_without_composition >= _SELF_GEN_INTERVAL:
                    _polls_without_composition = 0
                    mode = random.choice(_self_gen_modes)
                    print(f"\n  [self] No compositions in {_SELF_GEN_INTERVAL} cycles — generating ({mode})...")
                    try:
                        sg_result = await asyncio.wait_for(
                            asyncio.to_thread(engine.self_generate, mode=mode, dry_run=dry_run),
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
                            await asyncio.sleep(65)
                    except asyncio.TimeoutError:
                        print("  [self] TIMEOUT on self-generation")
                    except Exception as e:
                        print(f"  [self] ERROR: {e}")
            else:
                _polls_without_composition = 0
            _composed_this_cycle = False
            continue

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    engine.process,
                    stimulus=post["text"],
                    source=source,
                    stimulus_uri=post["post_uri"],
                    stimulus_author=post.get("author_handle") or post["author_did"],
                    dry_run=dry_run,
                ),
                timeout=120,
            )
        except asyncio.TimeoutError:
            print(f"\n  TIMEOUT processing post: {post['text'][:80]}")
            logger.info(f"\n--- TIMEOUT ---\nStimulus: {post['text'][:200]}")
            continue
        except Exception as e:
            print(f"\n  ERROR processing post: {e}")
            logger.info(f"\n--- ERROR ---\nStimulus: {post['text'][:200]}\nError: {e}")
            continue

        _print_result(post, result, dry_run)
        _log_interaction(logger, post, result, dry_run)

        # Track if we composed anything this cycle
        if not result.get("rate_limited"):
            comp = result.get("composition") or {}
            if comp.get("decision") == "post":
                _composed_this_cycle = True

        # In timeline mode, respect cooldown between posts
        if timeline and not result.get("rate_limited"):
            comp = result.get("composition") or {}
            if comp.get("decision") == "post":
                await asyncio.sleep(65)  # just over the 60s cooldown

        # If live and composition says post, actually post
        if live and not result.get("rate_limited"):
            comp = result.get("composition") or {}
            if comp.get("decision") == "post" and comp.get("posts"):
                try:
                    reply_to = None
                    if post["post_uri"]:
                        resolved = bsky_client.resolve_post(post["post_uri"])
                        reply_to = {"uri": resolved["uri"], "cid": resolved["cid"]}

                    posted = bsky_client.send_thread(comp["posts"], reply_to=reply_to)
                    uris = [p["uri"] for p in posted]
                    print(f"  POSTED: {uris}")

                    # Update the interaction with response URIs
                    store._conn.execute(
                        "UPDATE interactions SET response_uris = ? WHERE id = ?",
                        (__import__("json").dumps(uris), result["interaction_id"]),
                    )
                    store._conn.commit()
                except Exception as e:
                    print(f"  ERROR posting: {e}")


def main():
    parser = argparse.ArgumentParser(description="Lucubrator — Bluesky runner")
    parser.add_argument("--live", action="store_true", help="Actually post (default: dry run)")
    parser.add_argument(
        "--timeline", action="store_true",
        help="Poll your timeline instead of firehose (blog-only, always dry-run)",
    )
    args = parser.parse_args()

    if args.live and args.timeline:
        print("ERROR: --timeline is blog-only mode and cannot be combined with --live.")
        sys.exit(1)

    # Graceful shutdown on Ctrl-C
    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        print("\n  Shutting down...")
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(run(live=args.live, timeline=args.timeline))
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
