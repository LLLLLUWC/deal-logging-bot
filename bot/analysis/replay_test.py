"""Replay historical messages through the bot's classification logic.

This script simulates the bot's message processing without actually
calling external APIs (Notion, Anthropic, DocSend), to validate
classification accuracy against historical data.
"""

import json
import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import re

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bot.utils.link_detector import LinkDetector, LinkType, DetectedLink


@dataclass
class SimulatedMessage:
    """Simulates a Telegram Message for testing."""

    message_id: int
    text: str
    caption: Optional[str]
    date: datetime
    from_user_name: str
    from_user_is_bot: bool
    has_document: bool
    document_file_name: Optional[str]
    has_photo: bool
    is_forwarded: bool
    forward_from: Optional[str]
    reply_to_message_id: Optional[int]


@dataclass
class ClassificationResult:
    """Result of classifying a message."""

    message_id: int
    should_process: bool
    filter_reason: str
    looks_like_deal: bool
    detected_links: list[DetectedLink] = field(default_factory=list)
    best_deck_link: Optional[DetectedLink] = None
    original_text: str = ""
    sender: str = ""
    date: Optional[datetime] = None


class MessageClassifier:
    """Simulates the bot's message classification logic."""

    # Deal keywords (from message_handler.py)
    DEAL_KEYWORDS = [
        "docsend", "pitch", "deck", "investment", "funding", "series",
        "seed", "pre-seed", "raise", "round", "valuation", "cap", "safe",
        "equity", "tokenomics", "whitepaper", "intro", "meet", "connect",
        "founder", "startup", "project", "protocol", "platform",
    ]

    URL_PATTERNS = [
        "docsend.com", "papermark.io", "papermark.com", ".pdf", "notion.so",
        "pitch.com", "docs.google.com", "loom.com",
    ]

    def __init__(self):
        self.link_detector = LinkDetector()

    def classify(self, message: SimulatedMessage) -> ClassificationResult:
        """Classify a message using the same logic as the bot.

        Args:
            message: SimulatedMessage to classify.

        Returns:
            ClassificationResult with classification details.
        """
        result = ClassificationResult(
            message_id=message.message_id,
            should_process=False,
            filter_reason="",
            looks_like_deal=False,
            original_text=message.text or message.caption or "",
            sender=message.from_user_name,
            date=message.date,
        )

        # Skip messages from bots
        if message.from_user_is_bot:
            result.filter_reason = "from bot"
            return result

        # Check for content
        has_text = bool(message.text or message.caption)
        text = message.text or message.caption or ""

        # Skip if no content at all
        if not has_text and not message.has_document and not message.has_photo:
            result.filter_reason = "no content"
            return result

        # Accept any message with document/photo
        if message.has_document or message.has_photo:
            result.should_process = True
            result.filter_reason = "has attachment"
            result.looks_like_deal = self._looks_like_deal(message)
            result.detected_links = self.link_detector.detect_links(text)
            result.best_deck_link = self.link_detector.get_best_deck_link(text)
            return result

        # Skip very short messages
        if len(text) < 5:
            result.filter_reason = f"too short ({len(text)} chars)"
            return result

        # Accept forwarded messages
        if message.is_forwarded:
            result.should_process = True
            result.filter_reason = "forwarded message"
            result.looks_like_deal = self._looks_like_deal(message)
            result.detected_links = self.link_detector.detect_links(text)
            result.best_deck_link = self.link_detector.get_best_deck_link(text)
            return result

        # Check for deal-like content
        looks_like_deal = self._looks_like_deal(message)
        if looks_like_deal:
            result.should_process = True
            result.filter_reason = "looks like deal"
            result.looks_like_deal = True
            result.detected_links = self.link_detector.detect_links(text)
            result.best_deck_link = self.link_detector.get_best_deck_link(text)
            return result

        # Accept longer messages
        if len(text) >= 50:
            result.should_process = True
            result.filter_reason = "long message"
            result.detected_links = self.link_detector.detect_links(text)
            result.best_deck_link = self.link_detector.get_best_deck_link(text)
            return result

        result.filter_reason = "no deal keywords found"
        return result

    def _looks_like_deal(self, message: SimulatedMessage) -> bool:
        """Check if a message looks like a deal."""
        # Has PDF attachment
        if message.has_document:
            file_name = message.document_file_name or ""
            if file_name.lower().endswith(".pdf"):
                return True

        text = (message.text or message.caption or "").lower()

        # Check for deal-related keywords
        for keyword in self.DEAL_KEYWORDS:
            if keyword in text:
                return True

        # Check for common URL patterns
        for pattern in self.URL_PATTERNS:
            if pattern in text:
                return True

        # Forwarded messages need minimum substance to be considered deals
        # This reduces false positives on short forwarded questions/greetings
        if message.is_forwarded:
            # Forwarded messages with substantial content (100+ chars) are likely deals
            if len(text) >= 100:
                return True
            # Short forwarded messages need a URL to be considered deals
            if "http" in text:
                return True

        return False


class ReplayTester:
    """Replays historical messages through classification logic."""

    def __init__(self):
        self.classifier = MessageClassifier()

    def load_export(self, json_path: str) -> list[SimulatedMessage]:
        """Load messages from Telegram export JSON.

        Args:
            json_path: Path to the JSON export file.

        Returns:
            List of SimulatedMessage objects.
        """
        path = Path(json_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        messages = []
        for msg in data.get("messages", []):
            if msg.get("type") != "message":
                continue

            sim_msg = self._convert_message(msg)
            if sim_msg:
                messages.append(sim_msg)

        return messages

    def _convert_message(self, msg: dict) -> Optional[SimulatedMessage]:
        """Convert export message to SimulatedMessage."""
        try:
            date_str = msg.get("date", "")
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            date = datetime.now()

        # Get sender info
        sender = msg.get("from", "Unknown")
        if isinstance(sender, dict):
            sender = f"{sender.get('first_name', '')} {sender.get('last_name', '')}".strip()

        # Extract text
        text = self._extract_text(msg)

        # Check for attachments
        has_document = bool(msg.get("file"))
        document_file_name = msg.get("file_name") if has_document else None
        has_photo = bool(msg.get("photo"))

        # Check forwarding
        is_forwarded = bool(msg.get("forwarded_from"))
        forward_from = msg.get("forwarded_from")

        return SimulatedMessage(
            message_id=msg.get("id", 0),
            text=text,
            caption=None,  # In export, text already includes caption
            date=date,
            from_user_name=sender,
            from_user_is_bot=False,  # Can't determine from export
            has_document=has_document,
            document_file_name=document_file_name,
            has_photo=has_photo,
            is_forwarded=is_forwarded,
            forward_from=forward_from,
            reply_to_message_id=msg.get("reply_to_message_id"),
        )

    def _extract_text(self, msg: dict) -> str:
        """Extract text content from a message."""
        text = msg.get("text", "")

        if isinstance(text, str):
            return text

        if isinstance(text, list):
            parts = []
            for item in text:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("text", ""))
            return "".join(parts)

        return ""

    def run_test(
        self,
        json_path: str,
        expected_deals_file: Optional[str] = None,
    ) -> dict:
        """Run replay test on exported messages.

        Args:
            json_path: Path to Telegram export JSON.
            expected_deals_file: Optional CSV file with expected deal message IDs.

        Returns:
            Test results dict.
        """
        messages = self.load_export(json_path)
        results: list[ClassificationResult] = []

        # Load expected deals if provided
        expected_deal_ids = set()
        if expected_deals_file:
            with open(expected_deals_file, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    expected_deal_ids.add(int(row.get("message_id", 0)))

        # Classify all messages
        for msg in messages:
            result = self.classifier.classify(msg)
            results.append(result)

        # Calculate statistics
        total = len(results)
        processed = sum(1 for r in results if r.should_process)
        deals = sum(1 for r in results if r.looks_like_deal)
        with_deck_links = sum(1 for r in results if r.best_deck_link)

        # Filter reason distribution
        filter_reasons = {}
        for r in results:
            filter_reasons[r.filter_reason] = filter_reasons.get(r.filter_reason, 0) + 1

        # Link type distribution among processed
        link_types = {}
        for r in results:
            for link in r.detected_links:
                lt = link.link_type.value
                link_types[lt] = link_types.get(lt, 0) + 1

        # Accuracy metrics (if expected deals provided)
        accuracy_metrics = None
        if expected_deal_ids:
            detected_ids = {r.message_id for r in results if r.looks_like_deal}

            true_positives = len(expected_deal_ids & detected_ids)
            false_positives = len(detected_ids - expected_deal_ids)
            false_negatives = len(expected_deal_ids - detected_ids)
            true_negatives = total - true_positives - false_positives - false_negatives

            precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
            recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

            accuracy_metrics = {
                "true_positives": true_positives,
                "false_positives": false_positives,
                "false_negatives": false_negatives,
                "true_negatives": true_negatives,
                "precision": precision,
                "recall": recall,
                "f1_score": f1,
                "false_positive_examples": [
                    {"id": r.message_id, "text": r.original_text[:100]}
                    for r in results
                    if r.looks_like_deal and r.message_id not in expected_deal_ids
                ][:10],
                "false_negative_examples": [
                    {"id": r.message_id, "text": r.original_text[:100]}
                    for r in results
                    if not r.looks_like_deal and r.message_id in expected_deal_ids
                ][:10],
            }

        return {
            "total_messages": total,
            "processed": processed,
            "deals_detected": deals,
            "with_deck_links": with_deck_links,
            "filter_reasons": filter_reasons,
            "link_types": link_types,
            "accuracy_metrics": accuracy_metrics,
            "results": results,  # Full results for detailed analysis
        }

    def export_results(
        self,
        results: list[ClassificationResult],
        output_path: str,
        format: str = "csv",
    ) -> None:
        """Export classification results.

        Args:
            results: List of ClassificationResult.
            output_path: Output file path.
            format: "csv" or "json".
        """
        if format == "csv":
            self._export_csv(results, output_path)
        else:
            self._export_json(results, output_path)

    def _export_csv(self, results: list[ClassificationResult], output_path: str):
        """Export results to CSV."""
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "message_id", "date", "sender", "should_process",
                "filter_reason", "looks_like_deal", "deck_link_type",
                "deck_link_url", "text_preview",
            ])

            for r in results:
                writer.writerow([
                    r.message_id,
                    r.date.isoformat() if r.date else "",
                    r.sender,
                    r.should_process,
                    r.filter_reason,
                    r.looks_like_deal,
                    r.best_deck_link.link_type.value if r.best_deck_link else "",
                    r.best_deck_link.url if r.best_deck_link else "",
                    r.original_text[:100].replace("\n", " "),
                ])

    def _export_json(self, results: list[ClassificationResult], output_path: str):
        """Export results to JSON."""
        data = []
        for r in results:
            data.append({
                "message_id": r.message_id,
                "date": r.date.isoformat() if r.date else None,
                "sender": r.sender,
                "should_process": r.should_process,
                "filter_reason": r.filter_reason,
                "looks_like_deal": r.looks_like_deal,
                "detected_links": [
                    {"url": link.url, "type": link.link_type.value, "is_deck": link.is_deck}
                    for link in r.detected_links
                ],
                "best_deck_link": {
                    "url": r.best_deck_link.url,
                    "type": r.best_deck_link.link_type.value,
                } if r.best_deck_link else None,
                "text_preview": r.original_text[:200],
            })

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    """CLI entry point for replay testing."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Replay historical messages through bot classification"
    )
    parser.add_argument(
        "json_path",
        help="Path to Telegram export JSON file"
    )
    parser.add_argument(
        "-e", "--expected",
        help="CSV file with expected deal message IDs",
        default=None,
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path for results",
        default=None,
    )
    parser.add_argument(
        "-f", "--format",
        choices=["csv", "json"],
        default="csv",
        help="Output format (default: csv)",
    )

    args = parser.parse_args()

    tester = ReplayTester()

    print(f"Loading: {args.json_path}")
    results = tester.run_test(args.json_path, args.expected)

    print("\n" + "=" * 50)
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

        if metrics['false_positive_examples']:
            print("\nFalse positive examples:")
            for ex in metrics['false_positive_examples'][:5]:
                print(f"  - [{ex['id']}] {ex['text'][:60]}...")

        if metrics['false_negative_examples']:
            print("\nFalse negative examples:")
            for ex in metrics['false_negative_examples'][:5]:
                print(f"  - [{ex['id']}] {ex['text'][:60]}...")

    if args.output:
        tester.export_results(results['results'], args.output, args.format)
        print(f"\nResults exported to: {args.output}")

    return 0


if __name__ == "__main__":
    exit(main())
