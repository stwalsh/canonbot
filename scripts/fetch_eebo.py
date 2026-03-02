#!/usr/bin/env python3
"""
fetch_eebo.py — Download target EEBO-TCP poetry texts from GitHub.

1. Downloads TCP.csv (the master catalogue)
2. Filters for target poets (free texts only)
3. Writes corpus/eebo_manifest.csv
4. Downloads each XML file into corpus/raw/
"""

import csv
import io
import os
import sys
import time
from pathlib import Path

import requests
import yaml


def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def download_tcp_csv(url: str) -> list[dict]:
    """Download and parse the EEBO-TCP master CSV catalogue."""
    print(f"Downloading TCP.csv from {url} ...")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    # TCP.csv is UTF-8 with BOM sometimes; handle gracefully
    text = resp.text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    print(f"  Loaded {len(rows)} records from TCP.csv")
    return rows


def filter_for_poets(rows: list[dict], target_poets: list[str]) -> list[dict]:
    """
    Filter TCP.csv rows for target poets.
    Matches are case-insensitive substring matches against the Author field.
    Only includes rows with Status = "Free".
    """
    matches = []
    target_lower = [p.lower() for p in target_poets]

    for row in rows:
        author = row.get("Author", "")
        status = row.get("Status", "")

        # Only free texts
        if status.strip().lower() != "free":
            continue

        # Check if any target poet name appears in the author field
        author_lower = author.lower()
        for poet in target_lower:
            if poet in author_lower:
                matches.append(row)
                break

    return matches


def write_manifest(matches: list[dict], manifest_path: str) -> None:
    """Write the filtered manifest CSV."""
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)

    fieldnames = ["TCP_ID", "Author", "Title", "Date", "Status"]
    with open(manifest_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in matches:
            writer.writerow({
                "TCP_ID": row.get("TCP", row.get("TCP ID", row.get("TCP_ID", ""))).strip(),
                "Author": row.get("Author", "").strip(),
                "Title": row.get("Title", "").strip(),
                "Date": row.get("Date", "").strip(),
                "Status": row.get("Status", "").strip(),
            })

    print(f"  Wrote {len(matches)} entries to {manifest_path}")


def download_xml_files(
    manifest_path: str,
    raw_dir: str,
    xml_base_url: str,
    delay: float = 0.5,
) -> None:
    """Download XML files for each entry in the manifest."""
    os.makedirs(raw_dir, exist_ok=True)

    with open(manifest_path) as f:
        reader = csv.DictReader(f)
        entries = list(reader)

    total = len(entries)
    downloaded = 0
    skipped = 0
    failed = 0

    for i, entry in enumerate(entries, 1):
        tcp_id = entry["TCP_ID"]
        dest = os.path.join(raw_dir, f"{tcp_id}.xml")

        if os.path.exists(dest):
            skipped += 1
            continue

        url = xml_base_url.format(tcp_id=tcp_id)
        print(f"  [{i}/{total}] Downloading {tcp_id} ...", end=" ", flush=True)

        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            with open(dest, "wb") as out:
                out.write(resp.content)
            downloaded += 1
            print("OK")
        except requests.RequestException as e:
            failed += 1
            print(f"FAILED: {e}")

        # Be polite to GitHub
        time.sleep(delay)

    print(f"\nDownload complete: {downloaded} new, {skipped} already present, {failed} failed")


def print_manifest_summary(manifest_path: str) -> None:
    """Print a summary of poets and text counts in the manifest."""
    with open(manifest_path) as f:
        reader = csv.DictReader(f)
        entries = list(reader)

    # Group by author surname (rough)
    from collections import Counter
    authors = Counter()
    for entry in entries:
        # Take the surname (first part before comma)
        author = entry["Author"]
        surname = author.split(",")[0].strip() if "," in author else author.strip()
        authors[surname] += 1

    print(f"\nManifest summary ({len(entries)} total texts):")
    print("-" * 50)
    for author, count in authors.most_common():
        print(f"  {author:<30} {count:>4} texts")
    print("-" * 50)


def main():
    config = load_config()

    # Step 1: Download TCP.csv
    rows = download_tcp_csv(config["eebo"]["csv_url"])

    # Step 2: Filter for target poets
    matches = filter_for_poets(rows, config["target_poets"])
    print(f"  Found {len(matches)} free texts matching target poets")

    # Step 3: Write manifest
    manifest_path = config["paths"]["eebo_manifest"]
    write_manifest(matches, manifest_path)
    print_manifest_summary(manifest_path)

    # Step 4: Download XMLs
    if "--no-download" in sys.argv:
        print("\nSkipping XML download (--no-download flag set)")
    else:
        print(f"\nDownloading XML files to {config['paths']['corpus_raw']}/ ...")
        download_xml_files(
            manifest_path=manifest_path,
            raw_dir=config["paths"]["corpus_raw"],
            xml_base_url=config["eebo"]["xml_base_url"],
        )


if __name__ == "__main__":
    main()
