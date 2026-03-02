#!/usr/bin/env python3
"""Local test harness for the brain pipeline.

Runs invented stimuli through triage → retrieval → composition,
printing results to stdout. No Bluesky connection.

Usage:
    ANTHROPIC_API_KEY=sk-... ./venv/bin/python scripts/test_brain.py
"""

import json
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from src.brain import run

# Mix of good candidates and things the bot should ignore.
TEST_STIMULI = [
    # Should engage — mortality, grief
    "My grandmother died last week and I keep finding her handwriting in old books. "
    "It's like she's still annotating the world.",

    # Should engage — solitude, perception
    "There's a particular quality of light at 4am that makes you feel like "
    "the only person alive. Not lonely, just singular.",

    # Should engage — ambition, power
    "Watching politicians promise the same things decade after decade. "
    "The appetite for power never changes, only the vocabulary.",

    # Should skip — tech announcement
    "Just shipped v2.3.1 of our API! New rate limiting and better error messages. "
    "Check the changelog for details.",

    # Should skip — meme/joke
    "me: I should go to bed early tonight\nalso me at 3am: what if dogs had thumbs",

    # Should engage — language, memory
    "I've been thinking about how we lose words. Not forgetting vocabulary, "
    "but how certain words just fall out of common use and take whole ways of seeing with them.",

    # Should skip — already quoting poetry
    "\"Do I dare to eat a peach?\" Eliot knew something about the paralysis of self-consciousness "
    "that no therapist has matched since.",

    # Should engage — nature, wonder
    "Stood in the garden at dusk watching starlings murmur. "
    "Thousands of birds moving as one body. Language fails.",
]


def main():
    client = anthropic.Anthropic()

    for i, stimulus in enumerate(TEST_STIMULI, 1):
        print(f"\n{'='*72}")
        print(f"STIMULUS {i}:")
        print(f"  {stimulus[:100]}{'...' if len(stimulus) > 100 else ''}")
        print(f"{'='*72}")

        try:
            result = run(client, stimulus)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        t = result["triage"]
        print(f"\n  TRIAGE: {'YES' if t['engage'] else 'NO'} — {t['reason']}")

        if not t["engage"]:
            continue

        passages = result["passages"]
        print(f"\n  PASSAGES ({len(passages)} retrieved):")
        for j, p in enumerate(passages, 1):
            print(f"    {j}. {p['poet']} — \"{p['poem_title']}\" ({p['date']})")
            preview = p["text"][:80].replace("\n", " ")
            print(f"       {preview}...")
            print(f"       distance: {p['distance']:.4f}")

        comp = result["composition"]
        print(f"\n  COMPOSITION: {comp['decision'].upper()}")
        if comp["decision"] == "post":
            for k, post in enumerate(comp["posts"], 1):
                text = post["text"] if isinstance(post, dict) else post
                print(f"    Post {k} ({len(text)} chars):")
                print(f"      {text}")
            pu = comp.get("passage_used", {})
            if pu:
                print(f"    Passage used: {pu.get('poet', '?')} — \"{pu.get('poem_title', '?')}\"")
        elif comp.get("skip_reason"):
            print(f"    Skip reason: {comp['skip_reason']}")

    print(f"\n{'='*72}")
    print("Done.")


if __name__ == "__main__":
    main()
