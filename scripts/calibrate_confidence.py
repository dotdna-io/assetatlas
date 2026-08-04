#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Confidence score calibration tool.

Usage:
    python scripts/calibrate_confidence.py --input validation_set.csv --output calibration_report.json

Takes a CSV with columns: asset_id, predicted_category, actual_category, confidence
Outputs calibration metrics by confidence bucket.

A well-calibrated model should have accuracy ~ confidence for each bucket.
For example, assets with 0.8 confidence should be correctly categorized ~80% of the time.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict


def load_validation_set(path):
    """Load validation dataset from CSV.

    Expected columns: asset_id, predicted_category, actual_category, confidence
    """
    data = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        required = {"predicted_category", "actual_category", "confidence"}
        if not required.issubset(set(reader.fieldnames or [])):
            print(f"Error: CSV must have columns: {required}", file=sys.stderr)
            sys.exit(1)
        for row in reader:
            try:
                data.append(
                    {
                        "asset_id": row.get("asset_id", ""),
                        "predicted_category": row["predicted_category"].strip().lower(),
                        "actual_category": row["actual_category"].strip().lower(),
                        "confidence": float(row["confidence"]),
                    }
                )
            except (ValueError, KeyError) as e:
                print(f"Warning: skipping invalid row: {e}", file=sys.stderr)
    return data


def calculate_calibration(data):
    """Group by confidence bucket and calculate actual accuracy.

    Returns dict of bucket -> {count, accuracy, calibration_error}.
    """
    buckets = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in data:
        bucket = round(row["confidence"], 1)  # 0.0, 0.1, ..., 1.0
        buckets[bucket]["total"] += 1
        if row["predicted_category"] == row["actual_category"]:
            buckets[bucket]["correct"] += 1

    results = {}
    total_error = 0.0
    total_count = 0
    for bucket, counts in sorted(buckets.items()):
        accuracy = counts["correct"] / counts["total"] if counts["total"] > 0 else 0
        cal_error = abs(accuracy - bucket)
        results[f"{bucket:.1f}"] = {
            "count": counts["total"],
            "accuracy": round(accuracy, 4),
            "calibration_error": round(cal_error, 4),
        }
        total_error += cal_error * counts["total"]
        total_count += counts["total"]

    # Add summary metrics
    results["_summary"] = {
        "total_samples": total_count,
        "mean_calibration_error": round(total_error / total_count, 4)
        if total_count > 0
        else 0,
        "overall_accuracy": round(
            sum(b["correct"] for b in buckets.values()) / total_count, 4
        )
        if total_count > 0
        else 0,
    }
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate confidence scores against validation set"
    )
    parser.add_argument("--input", required=True, help="Validation CSV path")
    parser.add_argument(
        "--output", default="calibration_report.json", help="Output path"
    )
    args = parser.parse_args()

    data = load_validation_set(args.input)
    if not data:
        print("Error: no valid data in input file", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(data)} validation samples")
    results = calculate_calibration(data)

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary
    summary = results.get("_summary", {})
    print(f"\nCalibration Report:")
    print(f"  Samples: {summary.get('total_samples', 0)}")
    print(f"  Overall accuracy: {summary.get('overall_accuracy', 0):.1%}")
    print(f"  Mean calibration error: {summary.get('mean_calibration_error', 0):.4f}")
    print(f"\nFull report: {args.output}")


if __name__ == "__main__":
    main()
