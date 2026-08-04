#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Master script: runs all scrapers then normalizes output."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers import ALL_SCRAPERS
from normalize import normalize_and_write


def main():
    parser = argparse.ArgumentParser(description="Build device knowledge base")
    parser.add_argument("--output-dir", default=os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "device_knowledge_base"))
    parser.add_argument("--skip", nargs="*", default=[], help="Scrapers to skip")
    parser.add_argument("--cache-dir", default=os.path.join(
        os.path.dirname(__file__), "cache"))
    args = parser.parse_args()

    os.makedirs(args.cache_dir, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)

    all_results = []
    for name, scrape_fn in ALL_SCRAPERS.items():
        if name in args.skip:
            print(f"Skipping {name}")
            continue
        print(f"Running {name} scraper...")
        try:
            results = scrape_fn(cache_dir=args.cache_dir)
            print(f"  -> {len(results)} entries")
            all_results.extend(results)
        except Exception as e:
            print(f"  -> ERROR: {e}")

    print(f"\nTotal raw entries: {len(all_results)}")
    normalize_and_write(all_results, args.output_dir)
    print(f"Output written to: {args.output_dir}")


if __name__ == "__main__":
    main()
