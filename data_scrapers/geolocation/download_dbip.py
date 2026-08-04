#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Download the latest DB-IP Lite City MMDB database.

Usage:
    python data_scrapers/geolocation/download_dbip.py

Downloads to: data/dbip-city-lite.mmdb

Source: https://db-ip.com/db/download/ip-to-city-lite
License: CC-BY-4.0 (attribution required when displaying results)
"""

import gzip
import os
import sys
import urllib.request
from datetime import datetime


def get_download_url() -> str:
    """Build the download URL for the current month's database."""
    now = datetime.now()
    return (
        f"https://download.db-ip.com/free/"
        f"dbip-city-lite-{now.year}-{now.month:02d}.mmdb.gz"
    )


def download_dbip(output_dir: str = None) -> str:
    """Download and extract the DB-IP Lite City MMDB file."""
    if output_dir is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        output_dir = os.path.join(repo_root, "data")

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "dbip-city-lite.mmdb")

    url = get_download_url()
    gz_path = output_path + ".gz"

    print(f"Downloading DB-IP Lite City from: {url}")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AssetAtlas/1.0)"},
        )
        with urllib.request.urlopen(req) as response:
            with open(gz_path, "wb") as gz_file:
                gz_file.write(response.read())
    except Exception as e:
        print(f"Error downloading: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Extracting to: {output_path}")
    with gzip.open(gz_path, "rb") as gz_file:
        with open(output_path, "wb") as out_file:
            out_file.write(gz_file.read())

    os.remove(gz_path)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Done. File size: {size_mb:.1f} MB")

    return output_path


if __name__ == "__main__":
    download_dbip()
