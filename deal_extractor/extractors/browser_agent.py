"""AI-driven browser agent extractor using browser-use library.

This is a last-resort fallback extractor that uses an LLM-driven browser agent
to navigate pages requiring interaction (email gates, data rooms, complex JS).

Dependencies (optional, not in requirements.txt):
    pip install browser-use
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from .base import BaseExtractor
from ..models.types import FetchedDeck

logger = logging.getLogger(__name__)

# Maximum content length to return (match GenericWebExtractor)
_MAX_CONTENT_LENGTH = 8000

# Default task prompt template
_TASK_PROMPT = """Navigate to the following URL and extract the main text content of the page.

URL: {url}

Instructions:
1. Go to the URL
2. If there is an email gate or verification form, enter this email: {email}
3. If there is a password field, enter: {password}
4. If there are multiple pages/slides, navigate through all of them
5. Extract ALL the text content you can see on the page
6. Return the full text content as your final result

Important:
- Do NOT click on any download buttons or external links
- Focus on reading and extracting the visible text content
- If the page requires CAPTCHA, report that you cannot proceed
- If the page is empty or shows an error, report what you see
"""

_DOCSEND_TASK_PROMPT = """Navigate to the following DocSend URL and extract the document content.

URL: {url}

Instructions:
1. Go to the URL
2. If asked for an email, enter: {email}
3. If asked for a password, enter: {password}
4. Wait for the document viewer to load
5. Read through ALL pages/slides of the document
6. For each page, extract the visible text content
7. Use the navigation controls (arrows, page selector) to go through all pages
8. Return ALL the text content from every page

Important:
- This is a DocSend document viewer - look for page navigation controls
- Make sure to capture content from EVERY page, not just the first one
- If you see a CAPTCHA, report that you cannot proceed
"""


class BrowserAgentExtractor(BaseExtractor):
    """AI-driven browser agent fallback using browser-use library.

    Uses an LLM (via OpenAI-compatible API) to control a headless browser,
    handling interactive pages that static extractors cannot process.

    This extractor lazy-imports browser-use so that the rest of the
    codebase works without this heavy dependency.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        temp_dir: Optional[Path] = None,
        max_steps: int = 15,
        timeout: int = 120,
    ):
        """Initialize the browser agent extractor.

        Args:
            api_key: LLM API key (OpenAI-compatible).
            model: LLM model name (e.g. "kimi-k2.5").
            base_url: LLM API base URL (e.g. "https://api.moonshot.cn/v1").
            email: Email for authentication gates.
            password: Password for protected content.
            temp_dir: Directory for temporary files.
            max_steps: Maximum agent steps before stopping.
            timeout: Total timeout in seconds for agent execution.
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.email = email or ""
        self.password = password or ""
        self.temp_dir = Path(temp_dir or "./temp/browser_agent")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.max_steps = max_steps
        self.timeout = timeout

    async def extract(
        self, url: str, password: Optional[str] = None
    ) -> FetchedDeck:
        """Extract content from a URL using an AI browser agent.

        Args:
            url: The URL to extract content from.
            password: Optional password (overrides instance default).

        Returns:
            FetchedDeck with extracted content or error information.
        """
        # Lazy import — graceful failure if not installed
        try:
            from browser_use import Agent
            from browser_use.llm.openai.like import ChatOpenAILike
        except ImportError:
            return FetchedDeck(
                url=url,
                success=False,
                error="browser-use not installed (pip install browser-use)",
            )

        pw = password or self.password

        # Select task prompt based on URL
        if "docsend.com" in url:
            task = _DOCSEND_TASK_PROMPT.format(
                url=url, email=self.email, password=pw
            )
        else:
            task = _TASK_PROMPT.format(
                url=url, email=self.email, password=pw
            )

        logger.info(f"BrowserAgentExtractor: starting agent for {url}")

        try:
            # Create LLM instance (OpenAI-compatible via browser-use's own wrapper)
            # - temperature=1.0: Kimi K2.5 (thinking model) requires exactly 1
            # - frequency_penalty=None: disable for non-OpenAI provider compat
            # - remove_defaults_from_schema=True: Kimi rejects `default` alongside
            #   `anyOf` in json_schema — this strips defaults from the schema
            llm = ChatOpenAILike(
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=1.0,
                frequency_penalty=None,
                remove_defaults_from_schema=True,
                remove_min_items_from_schema=True,
            )

            # Create and run the browser agent
            agent = Agent(
                task=task,
                llm=llm,
                use_vision=False,  # DOM-only mode, cheaper and more compatible
                max_actions_per_step=3,
            )

            result = await asyncio.wait_for(
                agent.run(max_steps=self.max_steps),
                timeout=self.timeout,
            )

            # Extract final text from agent result
            content = self._extract_content_from_result(result)

            if not content or len(content.strip()) < 50:
                return FetchedDeck(
                    url=url,
                    success=False,
                    error=f"Browser agent extracted too little content ({len(content.strip()) if content else 0} chars)",
                )

            logger.info(
                f"BrowserAgentExtractor: extracted {len(content)} chars from {url}"
            )

            return FetchedDeck(
                url=url,
                success=True,
                content=content[:_MAX_CONTENT_LENGTH],
                title=None,
            )

        except asyncio.TimeoutError:
            logger.warning(f"BrowserAgentExtractor: timeout after {self.timeout}s for {url}")
            return FetchedDeck(
                url=url,
                success=False,
                error=f"Browser agent timed out after {self.timeout}s",
            )
        except Exception as e:
            logger.exception(f"BrowserAgentExtractor: error for {url}: {e}")
            return FetchedDeck(
                url=url,
                success=False,
                error=f"Browser agent error: {e}",
            )

    @staticmethod
    def _extract_content_from_result(result) -> str:
        """Extract text content from browser-use AgentHistoryList result.

        Args:
            result: AgentHistoryList returned by agent.run().

        Returns:
            Extracted text content.
        """
        # browser-use agent.run() returns an AgentHistoryList
        # The final result is in the last history item's result field
        try:
            final_result = result.final_result()
            if final_result:
                return str(final_result)
        except Exception:
            pass

        # Fallback: concatenate all extracted content from history
        try:
            texts = []
            for entry in result.history:
                if hasattr(entry, "result") and entry.result:
                    extracted = entry.result.extracted_content
                    if extracted:
                        texts.append(str(extracted))
            if texts:
                return "\n".join(texts)
        except Exception:
            pass

        return ""
