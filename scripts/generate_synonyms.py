#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Generate Synonym Lookup Table

Creates a JSON file mapping common IT terms to field names for
instant browser-side synonym resolution.

Usage:
    python scripts/generate_synonyms.py
    python scripts/generate_synonyms.py --analyze  # Show coverage analysis
    python scripts/generate_synonyms.py --detailed  # Show all matches with scores
"""

import os
import sys
import argparse
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.embedding import SynonymGenerator

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='Generate synonym lookup table')
    parser.add_argument('--analyze', action='store_true',
                        help='Show field coverage analysis')
    parser.add_argument('--detailed', action='store_true',
                        help='Show detailed match scores')
    parser.add_argument('--threshold', type=float, default=0.7,
                        help='Minimum similarity threshold (default: 0.7)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output path for JSON file')
    args = parser.parse_args()

    print("=" * 70)
    print("Synonym Lookup Table Generator")
    print("=" * 70)

    # Initialize generator
    print("\nInitializing embedding service...")
    generator = SynonymGenerator()

    if args.analyze:
        # Show coverage analysis
        print("\n" + "=" * 70)
        print("Field Coverage Analysis")
        print("=" * 70)

        coverage = generator.analyze_coverage()

        print(f"\nSummary:")
        print(f"  Total fields: {coverage['fields_total']}")
        print(f"  Fields with synonyms: {coverage['fields_with_synonyms']}")
        print(f"  Fields without synonyms: {coverage['fields_without_synonyms']}")
        print(f"  Coverage: {coverage['coverage_percentage']}%")

        print(f"\nFields WITHOUT synonyms (need more terms):")
        for field_name, stats in coverage['per_field'].items():
            if not stats['has_synonyms']:
                print(f"  - {field_name}")

        print(f"\nFields WITH synonyms (count, best score):")
        sorted_fields = sorted(
            [(k, v) for k, v in coverage['per_field'].items() if v['has_synonyms']],
            key=lambda x: x[1]['synonym_count'],
            reverse=True
        )
        for field_name, stats in sorted_fields:
            print(f"  - {field_name}: {stats['synonym_count']} synonyms, best={stats['best_match_score']}")

    elif args.detailed:
        # Show detailed matches
        print("\n" + "=" * 70)
        print("Detailed Match Results")
        print("=" * 70)

        matches = generator.get_detailed_matches()

        print(f"\nFound {len(matches)} matches above threshold\n")

        # Group by field
        by_field = {}
        for match in matches:
            by_field.setdefault(match.field_name, []).append(match)

        for field_name, field_matches in sorted(by_field.items()):
            print(f"\n{field_name} ({len(field_matches)} matches):")
            for m in sorted(field_matches, key=lambda x: x.similarity_score, reverse=True)[:5]:
                print(f"  {m.similarity_score:.3f} - '{m.term}'")

    else:
        # Generate lookup table
        print(f"\nGenerating lookup table (threshold={args.threshold})...")

        output_path = generator.save_lookup_table(
            output_path=args.output,
            min_similarity=args.threshold
        )

        # Load and display summary
        import json
        with open(output_path) as f:
            data = json.load(f)

        print("\n" + "=" * 70)
        print("Generation Complete")
        print("=" * 70)
        print(f"\nOutput: {output_path}")
        print(f"\nMetadata:")
        for key, value in data['_metadata'].items():
            print(f"  {key}: {value}")

        print(f"\nSample synonyms:")
        sample_synonyms = list(data['synonyms'].items())[:15]
        for term, field in sample_synonyms:
            print(f"  '{term}' → {field}")

        if len(data['synonyms']) > 15:
            print(f"  ... and {len(data['synonyms']) - 15} more")

        # Show file size
        import os
        file_size = os.path.getsize(output_path)
        print(f"\nFile size: {file_size:,} bytes ({file_size/1024:.1f} KB)")


if __name__ == '__main__':
    main()
