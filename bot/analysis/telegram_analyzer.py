"""Telegram export JSON analyzer for deal flow insights."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Import existing link detector for consistency
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bot.utils.link_detector import LinkDetector, LinkType


@dataclass
class MessageStats:
    """Statistics for a single message."""

    message_id: int
    date: datetime
    sender: str
    text: str
    has_attachment: bool
    attachment_type: Optional[str]  # document, photo, video, etc.
    file_name: Optional[str]
    is_forwarded: bool
    forward_from: Optional[str]
    reply_to_id: Optional[int]
    links: list[str] = field(default_factory=list)
    link_types: list[str] = field(default_factory=list)
    is_potential_deal: bool = False
    deal_confidence: str = "none"  # none, low, medium, high
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Complete analysis results."""

    total_messages: int = 0
    date_range: tuple[Optional[datetime], Optional[datetime]] = (None, None)

    # Message type distribution
    text_only: int = 0
    with_links: int = 0
    with_attachments: int = 0
    forwarded: int = 0
    replies: int = 0

    # Link type distribution
    link_type_counts: Counter = field(default_factory=Counter)
    all_links: list[str] = field(default_factory=list)

    # Deal detection
    potential_deals: int = 0
    deal_confidence_dist: Counter = field(default_factory=Counter)
    keyword_hits: Counter = field(default_factory=Counter)

    # Edge cases
    multi_link_messages: list[MessageStats] = field(default_factory=list)
    url_shortener_links: list[str] = field(default_factory=list)
    unclassified_links: list[str] = field(default_factory=list)
    long_messages: list[MessageStats] = field(default_factory=list)  # >1000 chars

    # Sender analysis
    sender_counts: Counter = field(default_factory=Counter)
    forwarded_sources: Counter = field(default_factory=Counter)

    # Failure candidates (messages that might be misclassified)
    likely_false_positives: list[MessageStats] = field(default_factory=list)
    likely_false_negatives: list[MessageStats] = field(default_factory=list)


class TelegramExportAnalyzer:
    """Analyzes Telegram JSON exports for deal flow patterns."""

    # Deal keywords (from message_handler.py)
    DEAL_KEYWORDS = [
        "docsend", "pitch", "deck", "investment", "funding", "series",
        "seed", "pre-seed", "raise", "round", "valuation", "cap", "safe",
        "equity", "tokenomics", "whitepaper", "intro", "meet", "connect",
        "founder", "startup", "project", "protocol", "platform",
    ]

    # Extended keywords for better detection
    EXTENDED_KEYWORDS = [
        "portfolio", "portfolio company", "deal", "opportunity", "looking for",
        "raising", "fundraise", "fundraising", "investor", "vc", "angel",
        "accelerator", "incubator", "y combinator", "yc", "batch",
        "defi", "nft", "web3", "crypto", "blockchain", "ai", "ml",
        "saas", "b2b", "b2c", "fintech", "healthtech", "edtech",
        "memo", "one-pager", "teaser", "dd", "due diligence",
        "term sheet", "lead investor", "follow-on",
    ]

    # URL shortener domains (excluding youtu.be which is now properly classified)
    URL_SHORTENERS = [
        "bit.ly", "t.co", "goo.gl", "tinyurl.com", "ow.ly", "is.gd",
        "buff.ly", "short.io", "rebrand.ly", "cutt.ly", "tiny.cc",
        "lnkd.in",
    ]

    # Non-deal patterns (to reduce false positives)
    NON_DEAL_PATTERNS = [
        r"^(hi|hello|hey|thanks|thank you|gm|gn|lol|haha)[\s!.]*$",
        r"^[👍🙏😀😂🎉]+$",  # Emoji-only messages
        r"^(yes|no|ok|okay|sure|got it)[\s!.]*$",
    ]

    def __init__(self):
        self.link_detector = LinkDetector()
        self._non_deal_regex = [re.compile(p, re.I) for p in self.NON_DEAL_PATTERNS]

    def load_export(self, json_path: str) -> dict:
        """Load a Telegram export JSON file.

        Args:
            json_path: Path to the JSON export file.

        Returns:
            Parsed JSON data.
        """
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"Export file not found: {json_path}")

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def analyze(self, json_path: str) -> AnalysisResult:
        """Analyze a Telegram export JSON file.

        Args:
            json_path: Path to the JSON export file.

        Returns:
            AnalysisResult with statistics and insights.
        """
        data = self.load_export(json_path)
        result = AnalysisResult()

        messages = data.get("messages", [])
        result.total_messages = len(messages)

        all_stats: list[MessageStats] = []
        dates: list[datetime] = []

        for msg in messages:
            stats = self._analyze_message(msg)
            if stats:
                all_stats.append(stats)
                dates.append(stats.date)
                self._update_result(result, stats)

        # Set date range
        if dates:
            result.date_range = (min(dates), max(dates))

        # Post-processing: identify failure candidates
        self._identify_failure_candidates(result, all_stats)

        return result

    def _analyze_message(self, msg: dict) -> Optional[MessageStats]:
        """Analyze a single message from the export.

        Args:
            msg: Message dict from Telegram export.

        Returns:
            MessageStats or None if message should be skipped.
        """
        # Skip service messages
        if msg.get("type") != "message":
            return None

        # Parse date
        try:
            date_str = msg.get("date", "")
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            date = datetime.now()

        # Get sender
        sender = msg.get("from", "Unknown")
        if isinstance(sender, dict):
            sender = sender.get("first_name", "") + " " + sender.get("last_name", "")

        # Extract text (can be string or list of text entities)
        text = self._extract_text(msg)

        # Check for attachments
        has_attachment = False
        attachment_type = None
        file_name = None

        if msg.get("photo"):
            has_attachment = True
            attachment_type = "photo"
        elif msg.get("file"):
            has_attachment = True
            attachment_type = "document"
            file_name = msg.get("file_name") or msg.get("file")
        elif msg.get("media_type"):
            has_attachment = True
            attachment_type = msg.get("media_type")

        # Check forwarding
        is_forwarded = bool(msg.get("forwarded_from"))
        forward_from = msg.get("forwarded_from")

        # Check reply
        reply_to_id = msg.get("reply_to_message_id")

        # Detect links
        links = self.link_detector.extract_urls(text)
        link_types = [self.link_detector.classify_url(url).value for url in links]

        # Analyze deal potential
        is_potential_deal, confidence, matched_keywords = self._assess_deal_potential(
            text, links, link_types, has_attachment, file_name, is_forwarded
        )

        return MessageStats(
            message_id=msg.get("id", 0),
            date=date,
            sender=sender,
            text=text,
            has_attachment=has_attachment,
            attachment_type=attachment_type,
            file_name=file_name,
            is_forwarded=is_forwarded,
            forward_from=forward_from,
            reply_to_id=reply_to_id,
            links=links,
            link_types=link_types,
            is_potential_deal=is_potential_deal,
            deal_confidence=confidence,
            matched_keywords=matched_keywords,
        )

    def _extract_text(self, msg: dict) -> str:
        """Extract text content from a message.

        Telegram exports can have text as either a string or a list of
        text entities with formatting.
        """
        text = msg.get("text", "")

        if isinstance(text, str):
            return text

        if isinstance(text, list):
            # Text is a list of entities
            parts = []
            for item in text:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("text", ""))
            return "".join(parts)

        return ""

    def _assess_deal_potential(
        self,
        text: str,
        links: list[str],
        link_types: list[str],
        has_attachment: bool,
        file_name: Optional[str],
        is_forwarded: bool,
    ) -> tuple[bool, str, list[str]]:
        """Assess whether a message is likely a deal.

        Returns:
            Tuple of (is_potential_deal, confidence, matched_keywords).
        """
        text_lower = text.lower()
        matched = []
        confidence_score = 0

        # Check for non-deal patterns first
        for pattern in self._non_deal_regex:
            if pattern.match(text_lower.strip()):
                return False, "none", []

        # PDF attachment is strong signal
        if has_attachment and file_name and file_name.lower().endswith(".pdf"):
            confidence_score += 3
            matched.append("pdf_attachment")

        # High-priority link types
        high_priority_types = ["docsend", "papermark", "pdf_direct", "loom", "pitch_com"]
        for lt in link_types:
            if lt in high_priority_types:
                confidence_score += 3
                matched.append(f"link_{lt}")

        # Forwarded messages in deal groups are often deals, but need substance
        # to avoid false positives on short forwarded questions/greetings
        if is_forwarded:
            if len(text) >= 100:
                # Substantial forwarded content
                confidence_score += 1
                matched.append("forwarded")
            elif links:
                # Short but has URL - still likely a deal
                confidence_score += 1
                matched.append("forwarded")

        # Check deal keywords
        for keyword in self.DEAL_KEYWORDS:
            if keyword in text_lower:
                confidence_score += 1
                matched.append(keyword)

        # Check extended keywords (lower weight)
        for keyword in self.EXTENDED_KEYWORDS:
            if keyword in text_lower and keyword not in matched:
                confidence_score += 0.5
                matched.append(keyword)

        # Longer text with links is more likely a deal
        if len(text) > 100 and links:
            confidence_score += 1

        # Determine confidence level
        if confidence_score >= 4:
            return True, "high", matched
        elif confidence_score >= 2:
            return True, "medium", matched
        elif confidence_score >= 1:
            return True, "low", matched

        return False, "none", matched

    def _update_result(self, result: AnalysisResult, stats: MessageStats) -> None:
        """Update analysis result with message stats."""
        # Message type counts
        if stats.has_attachment:
            result.with_attachments += 1
        if stats.links:
            result.with_links += 1
        if not stats.has_attachment and not stats.links:
            result.text_only += 1
        if stats.is_forwarded:
            result.forwarded += 1
        if stats.reply_to_id:
            result.replies += 1

        # Link types
        for lt in stats.link_types:
            result.link_type_counts[lt] += 1
        result.all_links.extend(stats.links)

        # Deal detection
        if stats.is_potential_deal:
            result.potential_deals += 1
        result.deal_confidence_dist[stats.deal_confidence] += 1

        for kw in stats.matched_keywords:
            result.keyword_hits[kw] += 1

        # Edge cases
        if len(stats.links) > 1:
            result.multi_link_messages.append(stats)

        if len(stats.text) > 1000:
            result.long_messages.append(stats)

        # Check for URL shorteners
        for link in stats.links:
            try:
                domain = urlparse(link).netloc.lower()
                if any(short in domain for short in self.URL_SHORTENERS):
                    result.url_shortener_links.append(link)
            except Exception:
                pass

        # Check for unclassified links
        for link, lt in zip(stats.links, stats.link_types):
            if lt in ("website", "unknown"):
                result.unclassified_links.append(link)

        # Sender stats
        result.sender_counts[stats.sender] += 1
        if stats.forward_from:
            result.forwarded_sources[stats.forward_from] += 1

    def _identify_failure_candidates(
        self,
        result: AnalysisResult,
        all_stats: list[MessageStats],
    ) -> None:
        """Identify messages that might be misclassified.

        False positives: Detected as deals but probably aren't.
        False negatives: Not detected as deals but probably are.
        """
        for stats in all_stats:
            # Potential false positives
            if stats.is_potential_deal:
                # Very short messages with only low-confidence keywords
                if len(stats.text) < 30 and stats.deal_confidence == "low":
                    result.likely_false_positives.append(stats)
                # Messages that are just greetings with a link
                elif len(stats.text) < 50 and not stats.has_attachment:
                    text_lower = stats.text.lower()
                    if any(g in text_lower for g in ["hi", "hello", "hey", "check"]):
                        result.likely_false_positives.append(stats)

            # Potential false negatives
            else:
                # Has deck link but not detected
                has_deck_link = any(
                    lt in ["docsend", "papermark", "pdf_direct"]
                    for lt in stats.link_types
                )
                if has_deck_link:
                    result.likely_false_negatives.append(stats)

                # Has PDF attachment but not detected
                if stats.has_attachment:
                    if stats.file_name and stats.file_name.lower().endswith(".pdf"):
                        result.likely_false_negatives.append(stats)

                # Long message with company-like content
                if len(stats.text) > 200:
                    text_lower = stats.text.lower()
                    company_signals = ["we are", "our team", "building", "solution"]
                    if any(s in text_lower for s in company_signals):
                        result.likely_false_negatives.append(stats)

    def generate_report(self, result: AnalysisResult) -> str:
        """Generate a human-readable report from analysis results.

        Args:
            result: AnalysisResult to format.

        Returns:
            Formatted report string.
        """
        lines = []
        lines.append("=" * 60)
        lines.append("TELEGRAM EXPORT ANALYSIS REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Overview
        lines.append("## Overview")
        lines.append(f"Total messages: {result.total_messages}")
        if result.date_range[0] and result.date_range[1]:
            lines.append(f"Date range: {result.date_range[0].date()} to {result.date_range[1].date()}")
        lines.append("")

        # Message type distribution
        lines.append("## Message Type Distribution")
        lines.append(f"  Text only: {result.text_only} ({result.text_only/result.total_messages*100:.1f}%)")
        lines.append(f"  With links: {result.with_links} ({result.with_links/result.total_messages*100:.1f}%)")
        lines.append(f"  With attachments: {result.with_attachments} ({result.with_attachments/result.total_messages*100:.1f}%)")
        lines.append(f"  Forwarded: {result.forwarded} ({result.forwarded/result.total_messages*100:.1f}%)")
        lines.append(f"  Replies: {result.replies} ({result.replies/result.total_messages*100:.1f}%)")
        lines.append("")

        # Link type distribution
        lines.append("## Link Type Distribution")
        for link_type, count in result.link_type_counts.most_common():
            lines.append(f"  {link_type}: {count}")
        lines.append(f"  Total unique links: {len(set(result.all_links))}")
        lines.append("")

        # Deal detection
        lines.append("## Deal Detection")
        lines.append(f"  Potential deals: {result.potential_deals} ({result.potential_deals/result.total_messages*100:.1f}%)")
        lines.append("  Confidence distribution:")
        for conf, count in sorted(result.deal_confidence_dist.items()):
            lines.append(f"    {conf}: {count}")
        lines.append("")

        lines.append("  Top matched keywords:")
        for kw, count in result.keyword_hits.most_common(15):
            lines.append(f"    {kw}: {count}")
        lines.append("")

        # Edge cases
        lines.append("## Edge Cases")
        lines.append(f"  Multi-link messages: {len(result.multi_link_messages)}")
        lines.append(f"  URL shortener links: {len(result.url_shortener_links)}")
        lines.append(f"  Unclassified links: {len(result.unclassified_links)}")
        lines.append(f"  Long messages (>1000 chars): {len(result.long_messages)}")
        lines.append("")

        # URL shorteners found
        if result.url_shortener_links:
            lines.append("  URL shortener examples:")
            for link in result.url_shortener_links[:5]:
                lines.append(f"    - {link}")
            lines.append("")

        # Unclassified links (show unique domains)
        if result.unclassified_links:
            domains = Counter()
            for link in result.unclassified_links:
                try:
                    domain = urlparse(link).netloc.lower()
                    domains[domain] += 1
                except Exception:
                    pass
            lines.append("  Top unclassified domains:")
            for domain, count in domains.most_common(10):
                lines.append(f"    {domain}: {count}")
            lines.append("")

        # Sender analysis
        lines.append("## Sender Analysis")
        lines.append("  Top senders:")
        for sender, count in result.sender_counts.most_common(10):
            lines.append(f"    {sender}: {count}")
        lines.append("")

        if result.forwarded_sources:
            lines.append("  Top forwarded sources:")
            for source, count in result.forwarded_sources.most_common(10):
                lines.append(f"    {source}: {count}")
            lines.append("")

        # Failure candidates
        lines.append("## Potential Misclassifications")
        lines.append(f"  Likely false positives: {len(result.likely_false_positives)}")
        lines.append(f"  Likely false negatives: {len(result.likely_false_negatives)}")
        lines.append("")

        if result.likely_false_positives:
            lines.append("  False positive examples (detected as deal but probably not):")
            for stats in result.likely_false_positives[:5]:
                preview = stats.text[:80].replace("\n", " ")
                lines.append(f"    - [{stats.deal_confidence}] {preview}...")
            lines.append("")

        if result.likely_false_negatives:
            lines.append("  False negative examples (not detected but might be deal):")
            for stats in result.likely_false_negatives[:5]:
                preview = stats.text[:80].replace("\n", " ")
                links_info = f" (links: {stats.link_types})" if stats.links else ""
                attach_info = f" (attachment: {stats.file_name})" if stats.file_name else ""
                lines.append(f"    - {preview}...{links_info}{attach_info}")
            lines.append("")

        lines.append("=" * 60)
        lines.append("END OF REPORT")
        lines.append("=" * 60)

        return "\n".join(lines)

    def export_detailed_json(self, result: AnalysisResult, output_path: str) -> None:
        """Export detailed analysis as JSON for further processing.

        Args:
            result: AnalysisResult to export.
            output_path: Path to write JSON file.
        """
        def stats_to_dict(stats: MessageStats) -> dict:
            return {
                "message_id": stats.message_id,
                "date": stats.date.isoformat(),
                "sender": stats.sender,
                "text": stats.text[:500],  # Truncate for readability
                "has_attachment": stats.has_attachment,
                "attachment_type": stats.attachment_type,
                "file_name": stats.file_name,
                "is_forwarded": stats.is_forwarded,
                "links": stats.links,
                "link_types": stats.link_types,
                "is_potential_deal": stats.is_potential_deal,
                "deal_confidence": stats.deal_confidence,
                "matched_keywords": stats.matched_keywords,
            }

        export_data = {
            "summary": {
                "total_messages": result.total_messages,
                "date_range": [
                    result.date_range[0].isoformat() if result.date_range[0] else None,
                    result.date_range[1].isoformat() if result.date_range[1] else None,
                ],
                "text_only": result.text_only,
                "with_links": result.with_links,
                "with_attachments": result.with_attachments,
                "forwarded": result.forwarded,
                "potential_deals": result.potential_deals,
            },
            "link_types": dict(result.link_type_counts),
            "keyword_hits": dict(result.keyword_hits),
            "edge_cases": {
                "multi_link_messages": [stats_to_dict(s) for s in result.multi_link_messages[:20]],
                "url_shortener_links": result.url_shortener_links[:50],
                "unclassified_links": result.unclassified_links[:50],
                "long_messages": [stats_to_dict(s) for s in result.long_messages[:20]],
            },
            "failure_candidates": {
                "likely_false_positives": [stats_to_dict(s) for s in result.likely_false_positives[:30]],
                "likely_false_negatives": [stats_to_dict(s) for s in result.likely_false_negatives[:30]],
            },
            "sender_stats": dict(result.sender_counts.most_common(50)),
            "forwarded_sources": dict(result.forwarded_sources.most_common(30)),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)


def main():
    """CLI entry point for analysis."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze Telegram export JSON for deal flow patterns"
    )
    parser.add_argument(
        "json_path",
        help="Path to Telegram export JSON file (result.json)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path for detailed JSON analysis",
        default=None,
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only output summary statistics",
    )

    args = parser.parse_args()

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
            print(f"False positives: {len(result.likely_false_positives)}")
            print(f"False negatives: {len(result.likely_false_negatives)}")

        if args.output:
            analyzer.export_detailed_json(result, args.output)
            print(f"\nDetailed analysis saved to: {args.output}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
