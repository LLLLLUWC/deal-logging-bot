#!/usr/bin/env python3
"""
Analyze Telegram export JSON for deal flow patterns.

Usage:
    # Basic analysis with report
    python analyze_export.py /path/to/result.json

    # Export detailed JSON for further processing
    python analyze_export.py /path/to/result.json -o analysis_output.json

    # Run replay test (simulate bot classification)
    python analyze_export.py /path/to/result.json --replay

    # Replay test with expected deals for accuracy measurement
    python analyze_export.py /path/to/result.json --replay -e expected_deals.csv
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from bot.analysis.telegram_analyzer import TelegramExportAnalyzer
from bot.analysis.replay_test import ReplayTester


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Telegram export for deal flow patterns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze and print report
  python analyze_export.py /path/to/ChatExport/result.json

  # Export detailed analysis as JSON
  python analyze_export.py /path/to/result.json -o detailed_analysis.json

  # Run replay test (classify messages with bot logic)
  python analyze_export.py /path/to/result.json --replay -o classified.csv

  # Measure accuracy against known deals
  python analyze_export.py /path/to/result.json --replay -e known_deals.csv
        """
    )

    parser.add_argument(
        "json_path",
        help="Path to Telegram export JSON file (usually result.json in the export folder)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path for detailed results (JSON for analysis, CSV/JSON for replay)",
        default=None,
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only output summary statistics",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Run replay test (simulate bot classification logic)",
    )
    parser.add_argument(
        "-e", "--expected",
        help="CSV file with expected deal message IDs (for replay accuracy testing)",
        default=None,
    )
    parser.add_argument(
        "-f", "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output format for replay results (default: csv)",
    )

    args = parser.parse_args()

    # Validate input file exists
    json_path = Path(args.json_path)
    if not json_path.exists():
        print(f"Error: File not found: {args.json_path}")
        return 1

    if args.replay:
        # Run replay test
        return run_replay_test(args)
    else:
        # Run analysis
        return run_analysis(args)


def run_analysis(args) -> int:
    """Run the analysis mode."""
    analyzer = TelegramExportAnalyzer()

    print(f"Analyzing: {args.json_path}")
    print()

    try:
        result = analyzer.analyze(args.json_path)

        if not args.quiet:
            report = analyzer.generate_report(result)
            print(report)
        else:
            print(f"Total: {result.total_messages}, Deals: {result.potential_deals}")
            print(f"Likely false positives: {len(result.likely_false_positives)}")
            print(f"Likely false negatives: {len(result.likely_false_negatives)}")

        if args.output:
            analyzer.export_detailed_json(result, args.output)
            print(f"\nDetailed analysis saved to: {args.output}")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


def run_replay_test(args) -> int:
    """Run the replay test mode."""
    tester = ReplayTester()

    print(f"Running replay test: {args.json_path}")
    print()

    try:
        results = tester.run_test(args.json_path, args.expected)

        print("=" * 50)
        print("REPLAY TEST RESULTS")
        print("=" * 50)
        print(f"\nTotal messages: {results['total_messages']}")
        print(f"Would be processed: {results['processed']}")
        print(f"Detected as deals: {results['deals_detected']}")
        print(f"With deck links: {results['with_deck_links']}")

        print("\nFilter reasons:")
        for reason, count in sorted(results['filter_reasons'].items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")

        print("\nLink types found:")
        for lt, count in sorted(results['link_types'].items(), key=lambda x: -x[1]):
            print(f"  {lt}: {count}")

        if results['accuracy_metrics']:
            print("\nAccuracy metrics:")
            metrics = results['accuracy_metrics']
            print(f"  Precision: {metrics['precision']:.3f}")
            print(f"  Recall: {metrics['recall']:.3f}")
            print(f"  F1 Score: {metrics['f1_score']:.3f}")
            print(f"  True Positives: {metrics['true_positives']}")
            print(f"  False Positives: {metrics['false_positives']}")
            print(f"  False Negatives: {metrics['false_negatives']}")

        if args.output:
            tester.export_results(results['results'], args.output, args.format)
            print(f"\nResults exported to: {args.output}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
