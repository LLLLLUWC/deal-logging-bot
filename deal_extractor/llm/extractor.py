"""Two-stage LLM extractor for deal information.

Stage 1: Router Agent - Analyzes message, decides if it's a deal
Stage 2: Extractor Agent - Processes content, extracts deal info
"""

import json
import logging
import re
from typing import Optional

from openai import OpenAI

from ..links import LinkDetector, LinkType
from ..models.types import Deal, ExtractionResult, FetchedDeck, RouterDecision
from .prompts import AVAILABLE_TAGS, EXTRACTOR_PROMPT, ROUTER_PROMPT

logger = logging.getLogger(__name__)


class LLMExtractor:
    """Two-stage LLM extractor for efficient deal extraction.

    Stage 1 (Router): Quick analysis - is this a deal?
    Stage 2 (Extractor): Deep analysis - extract deal information

    Supports any OpenAI-compatible API (Kimi, OpenAI, Azure, local LLMs).

    Example:
        extractor = LLMExtractor(
            api_key="sk-xxx",
            model="kimi-k2.5",
            base_url="https://api.moonshot.cn/v1",
        )
        result = await extractor.extract(
            message_text="Check out this deal: https://docsend.com/view/xxx",
            sender="John",
            fetched_decks=[...],
        )
    """

    # Default configuration
    DEFAULT_MODEL = "kimi-k2.5"
    DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """Initialize the LLM extractor.

        Args:
            api_key: API key for the LLM provider.
            model: Model name (default: kimi-k2.5).
            base_url: API base URL (default: Kimi/Moonshot).
        """
        self.model = model or self.DEFAULT_MODEL
        self.base_url = base_url or self.DEFAULT_BASE_URL

        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
        )

        self.link_detector = LinkDetector()
        logger.info(f"LLMExtractor initialized: model={self.model}, base_url={self.base_url}")

    async def extract(
        self,
        message_text: str,
        sender: str,
        fetched_decks: Optional[list[FetchedDeck]] = None,
        pdf_content: Optional[str] = None,
    ) -> ExtractionResult:
        """Extract deals from a message using two-stage approach.

        Args:
            message_text: The message content.
            sender: Message sender (OP Source).
            fetched_decks: Pre-fetched deck contents.
            pdf_content: Pre-extracted PDF content.

        Returns:
            ExtractionResult with extracted deals.
        """
        fetched_decks = fetched_decks or []

        # Include PDF content as a fetched deck
        if pdf_content:
            fetched_decks.append(
                FetchedDeck(
                    url="[PDF Attachment]",
                    success=True,
                    content=pdf_content,
                )
            )

        # Stage 1: Router
        pdf_attachment_info = None
        if pdf_content:
            if pdf_content.startswith("File: "):
                pdf_attachment_info = pdf_content.split("\n")[0].replace("File: ", "")
            else:
                pdf_attachment_info = "PDF attached"

        router_decision, router_tokens = await self._run_router(
            message_text, sender, pdf_attachment_info
        )

        logger.info(
            f"Router: is_deal={router_decision.is_deal}, "
            f"confidence={router_decision.confidence}, "
            f"reason={router_decision.reason}"
        )

        # Skip if not a deal
        if not router_decision.is_deal:
            return ExtractionResult(
                success=True,
                skipped_reason=router_decision.reason,
                router_tokens=router_tokens,
                total_tokens=router_tokens,
                router_confidence=router_decision.confidence,
                router_reason=router_decision.reason,
            )

        # Stage 2: Extractor
        deals, extractor_tokens = await self._run_extractor(
            message_text=message_text,
            sender=sender,
            fetched_decks=fetched_decks,
            company_hints=router_decision.company_hints,
        )

        # Auto-assign deck URLs
        deals = self._assign_deck_urls(deals, fetched_decks)

        total_tokens = router_tokens + extractor_tokens
        decks_fetched = len([d for d in fetched_decks if d.success])

        logger.info(
            f"Extractor: {len(deals)} deals, "
            f"tokens: router={router_tokens}, extractor={extractor_tokens}"
        )

        return ExtractionResult(
            success=True,
            deals=deals,
            router_tokens=router_tokens,
            extractor_tokens=extractor_tokens,
            total_tokens=total_tokens,
            decks_fetched=decks_fetched,
            router_confidence=router_decision.confidence,
            router_reason=router_decision.reason,
        )

    async def _run_router(
        self,
        message_text: str,
        sender: str,
        pdf_attachment: Optional[str] = None,
    ) -> tuple[RouterDecision, int]:
        """Run the Router Agent (Stage 1).

        Args:
            message_text: The message content.
            sender: Message sender.
            pdf_attachment: PDF attachment filename if present.

        Returns:
            Tuple of (RouterDecision, tokens_used).
        """
        prompt_parts = [f"Sender: {sender}"]
        if pdf_attachment:
            prompt_parts.append(
                f"PDF Attachment: {pdf_attachment} (TREAT AS DEAL)"
            )
        prompt_parts.append(f"\nMessage:\n{message_text}")

        user_prompt = "\n".join(prompt_parts)
        tokens = 0

        try:
            # Build request kwargs
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 500,
            }

            # Add thinking mode disable for Kimi
            if "kimi" in self.model.lower() or "moonshot" in self.base_url.lower():
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

            response = self.client.chat.completions.create(**kwargs)

            tokens = (
                response.usage.prompt_tokens + response.usage.completion_tokens
                if response.usage
                else 0
            )

            content = response.choices[0].message.content or ""
            data = self._extract_json(content)

            return (
                RouterDecision(
                    is_deal=data.get("is_deal", False),
                    confidence=data.get("confidence", 0.0),
                    reason=data.get("reason", ""),
                    company_hints=data.get("company_hints", []),
                    is_multi_deal=data.get("is_multi_deal", False),
                ),
                tokens,
            )

        except json.JSONDecodeError as e:
            logger.error(f"Router JSON error: {e}")
            # Fallback: assume might be a deal
            return (
                RouterDecision(
                    is_deal=True,
                    confidence=0.5,
                    reason="Router JSON error, assuming deal",
                ),
                tokens,
            )
        except Exception as e:
            logger.exception(f"Router error: {e}")
            return (
                RouterDecision(
                    is_deal=True,
                    confidence=0.5,
                    reason=f"Router error: {e}",
                ),
                0,
            )

    async def _run_extractor(
        self,
        message_text: str,
        sender: str,
        fetched_decks: list[FetchedDeck],
        company_hints: list[str],
    ) -> tuple[list[Deal], int]:
        """Run the Extractor Agent (Stage 2).

        Args:
            message_text: The message content.
            sender: Message sender.
            fetched_decks: List of fetched deck contents.
            company_hints: Company names from router.

        Returns:
            Tuple of (list of Deal, tokens_used).
        """
        # Build prompt
        prompt_parts = [
            "# Message Information",
            f"**Sender (OP Source)**: {sender}",
        ]

        if company_hints:
            prompt_parts.append(f"**Company Hints**: {', '.join(company_hints)}")

        prompt_parts.append(f"\n## Original Message\n{message_text}")

        # Add deck contents
        if fetched_decks:
            prompt_parts.append("\n## Fetched Deck Contents")
            for i, deck in enumerate(fetched_decks, 1):
                prompt_parts.append(f"\n### Deck {i}: {deck.url}")
                if deck.success and deck.content:
                    content = deck.content[:6000]
                    if len(deck.content) > 6000:
                        content += "\n[Content truncated...]"
                    prompt_parts.append(content)
                elif deck.error:
                    prompt_parts.append(f"(Fetch failed: {deck.error})")
                else:
                    prompt_parts.append("(No content extracted)")

        user_prompt = "\n".join(prompt_parts)
        tokens = 0

        try:
            # Build request kwargs
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": EXTRACTOR_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 8000,
            }

            # Add thinking mode disable for Kimi
            if "kimi" in self.model.lower() or "moonshot" in self.base_url.lower():
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

            response = self.client.chat.completions.create(**kwargs)

            tokens = (
                response.usage.prompt_tokens + response.usage.completion_tokens
                if response.usage
                else 0
            )

            content = response.choices[0].message.content or ""
            finish_reason = response.choices[0].finish_reason

            if finish_reason == "length":
                logger.warning("Extractor output truncated")

            data = self._extract_json(content)

            deals = []
            for deal_data in data.get("deals", []):
                tags = deal_data.get("tags", [])
                valid_tags = [t for t in tags if t in AVAILABLE_TAGS]

                deal = Deal(
                    company_name=deal_data.get("company_name", "Unknown"),
                    tags=valid_tags,
                    intro=deal_data.get("intro", ""),
                    detailed_content=deal_data.get("detailed_content", ""),
                    deck_url=deal_data.get("deck_url"),
                    external_source=deal_data.get("external_source"),
                    raise_amount=deal_data.get("raise_amount"),
                    valuation=deal_data.get("valuation"),
                )
                deals.append(deal)

            return deals, tokens

        except json.JSONDecodeError as e:
            logger.error(f"Extractor JSON error: {e}")
            return [], tokens
        except Exception as e:
            logger.exception(f"Extractor error: {e}")
            return [], 0

    def _extract_json(self, content: str) -> dict:
        """Extract JSON from LLM response.

        Handles:
        - Raw JSON
        - JSON wrapped in ```json ... ```
        - JSON wrapped in ``` ... ```

        Args:
            content: Raw LLM response.

        Returns:
            Parsed JSON dict.

        Raises:
            json.JSONDecodeError: If no valid JSON found.
        """
        if not content:
            raise json.JSONDecodeError("Empty content", "", 0)

        content = content.strip()

        # Try direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try markdown code block
        code_block_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        match = re.search(code_block_pattern, content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try to find JSON object
        brace_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        matches = re.findall(brace_pattern, content, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue

        preview = content[:200] if len(content) > 200 else content
        raise json.JSONDecodeError(f"No valid JSON in: {preview}", content, 0)

    def _assign_deck_urls(
        self,
        deals: list[Deal],
        fetched_decks: list[FetchedDeck],
    ) -> list[Deal]:
        """Auto-assign deck URLs to deals.

        Args:
            deals: List of extracted deals.
            fetched_decks: List of fetched decks.

        Returns:
            Deals with URLs assigned.
        """
        if not deals or not fetched_decks:
            return deals

        # Get valid URLs (exclude placeholders)
        deck_urls = [
            d.url
            for d in fetched_decks
            if d.url and not d.url.startswith("[")
        ]

        if not deck_urls:
            return deals

        # Find deals needing URLs
        deals_needing_urls = [d for d in deals if not d.deck_url]

        if not deals_needing_urls:
            return deals

        # Single deck -> assign to all
        if len(deck_urls) == 1:
            for deal in deals:
                if not deal.deck_url:
                    deal.deck_url = deck_urls[0]
            return deals

        # Same count -> match by position
        if len(deals) == len(deck_urls):
            for i, deal in enumerate(deals):
                if not deal.deck_url:
                    deal.deck_url = deck_urls[i]
            return deals

        # Try name matching
        for deal in deals:
            if deal.deck_url:
                continue

            company_lower = deal.company_name.lower()
            for url in deck_urls:
                url_lower = url.lower()
                if company_lower in url_lower or any(
                    word in url_lower
                    for word in company_lower.split()
                    if len(word) > 3
                ):
                    deal.deck_url = url
                    break

        # Fallback: assign by position
        unassigned = [d for d in deals if not d.deck_url]
        used = {d.deck_url for d in deals if d.deck_url}
        available = [u for u in deck_urls if u not in used]

        for deal, url in zip(unassigned, available):
            deal.deck_url = url

        return deals
