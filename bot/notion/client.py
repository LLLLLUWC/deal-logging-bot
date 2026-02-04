"""Notion API integration for deal logging."""

import time
from dataclasses import dataclass
from typing import Any, Optional

from notion_client import Client
from notion_client.errors import APIResponseError


@dataclass
class DealEntry:
    """Data for creating a deal entry in Notion."""

    title: str
    tags: list[str] = None
    op_source: Optional[str] = None
    external_source: Optional[str] = None
    deck_url: Optional[str] = None
    deck_file_path: Optional[str] = None  # Local PDF path for upload
    intro: str = ""  # Short intro (140 chars) for Intro field
    detailed_content: str = ""  # Full Markdown for page body

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class NotionCreateResult:
    """Result of creating a Notion page."""

    success: bool
    page_id: Optional[str] = None
    page_url: Optional[str] = None
    error: Optional[str] = None


class NotionClient:
    """Client for interacting with Notion API."""

    def __init__(
        self,
        api_key: str,
        database_id: str,
        field_mapping: Optional[dict[str, str]] = None,
    ):
        """Initialize the Notion client.

        Args:
            api_key: Notion integration API key.
            database_id: ID of the target database.
            field_mapping: Optional custom field name mapping.
        """
        self.client = Client(auth=api_key)
        self.database_id = database_id

        # Default field mapping (can be overridden)
        # Based on actual database schema:
        # - Name: title (page title)
        # - Tags: rich_text (not multi_select!)
        # - OP Source: multi_select
        # - External Source: rich_text
        # - Deck: files (Files & Media)
        # - Intro: rich_text (short intro, 140 chars)
        self.field_mapping = field_mapping or {
            "title": "Name",  # Page title field (title type)
            "tags": "Tags",  # rich_text - will store as comma-separated
            "op_source": "OP Source",  # multi_select
            "external_source": "External Source",  # rich_text
            "deck": "Deck",  # files (Files & Media for PDF upload)
            "intro": "Intro",  # rich_text (short intro)
        }

        # Hardcoded property types for when we can't read the database schema
        # (e.g., linked databases don't expose properties via API)
        self._fallback_property_types = {
            "Name": "title",
            "Tags": "rich_text",
            "OP Source": "multi_select",
            "External Source": "rich_text",
            "Deck": "files",  # Files & Media type
            "Memo": "rich_text",
            "Tab": "select",
            "Status": "select",
            "Owner": "multi_select",
            "Intro": "rich_text",
        }

        self._database_properties = None

    def _get_database_properties(self, force_refresh: bool = False) -> dict[str, Any]:
        """Fetch and cache database property schema.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data.

        Returns:
            Dictionary of property names to their configurations.
        """
        import logging
        logger = logging.getLogger(__name__)

        if self._database_properties is None or force_refresh:
            max_retries = 3
            last_error = None

            for attempt in range(max_retries):
                try:
                    db = self.client.databases.retrieve(database_id=self.database_id)
                    self._database_properties = db.get("properties", {})
                    logger.info(f"Fetched database properties: {list(self._database_properties.keys())}")
                    return self._database_properties
                except APIResponseError as e:
                    last_error = e
                    logger.error(f"Notion API error fetching properties (attempt {attempt + 1}): {e}")
                    # Don't cache on API errors - allow retry
                except Exception as e:
                    last_error = e
                    error_str = str(e).lower()
                    # Retry on SSL/connection errors
                    if any(x in error_str for x in ["ssl", "connection", "timeout", "eof"]):
                        logger.warning(f"Connection error fetching properties (attempt {attempt + 1}): {e}")
                        time.sleep(1 * (attempt + 1))  # Backoff
                        continue
                    else:
                        logger.error(f"Unexpected error fetching properties: {e}")
                        break

            # If all retries failed and we have no cached data, log error
            if self._database_properties is None:
                logger.error(f"Failed to fetch database properties after {max_retries} attempts: {last_error}")
                # Return empty dict but DON'T cache it (so next call will retry)
                return {}

        return self._database_properties

    def _build_property_value(
        self, prop_name: str, value: Any, prop_config: dict
    ) -> Optional[dict]:
        """Build a Notion property value based on its type.

        Args:
            prop_name: Property name.
            value: Value to set.
            prop_config: Property configuration from database schema.

        Returns:
            Notion API property value dict, or None if value is empty.
        """
        if value is None or value == "":
            return None

        prop_type = prop_config.get("type")

        if prop_type == "title":
            return {
                "title": [{"text": {"content": str(value)}}]
            }

        elif prop_type == "rich_text":
            # Handle multiline text properly
            # Notion has a 2000 character limit per text block, but we can use multiple blocks
            if isinstance(value, list):
                content = ", ".join(str(v) for v in value)
            else:
                content = str(value)

            # Split into chunks of 2000 chars max
            chunks = []
            while content:
                chunk = content[:2000]
                content = content[2000:]
                chunks.append({"text": {"content": chunk}})

            return {"rich_text": chunks}

        elif prop_type == "multi_select":
            if isinstance(value, list):
                return {
                    "multi_select": [{"name": str(tag)} for tag in value]
                }
            else:
                return {
                    "multi_select": [{"name": str(value)}]
                }

        elif prop_type == "select":
            return {
                "select": {"name": str(value)}
            }

        elif prop_type == "url":
            return {
                "url": str(value)
            }

        elif prop_type == "files":
            # Files property - only supports external URLs via API
            # Local file paths need to be hosted elsewhere first
            value_str = str(value)
            if value_str.startswith(("http://", "https://")):
                return {
                    "files": [
                        {
                            "name": "Deck",
                            "type": "external",
                            "external": {"url": value_str}
                        }
                    ]
                }
            else:
                # Local file path - can't upload directly via Notion API
                # Return None, will need external hosting solution
                return None

        elif prop_type == "people":
            # People properties require user IDs, not names
            # We'll fall back to rich_text for names
            return None

        elif prop_type == "checkbox":
            return {
                "checkbox": bool(value)
            }

        elif prop_type == "number":
            try:
                return {
                    "number": float(value)
                }
            except (TypeError, ValueError):
                return None

        elif prop_type == "date":
            return {
                "date": {"start": str(value)}
            }

        # Unknown type - skip
        return None

    def _markdown_to_blocks(self, markdown_text: str) -> list[dict]:
        """Convert Markdown text to Notion blocks.

        Args:
            markdown_text: Markdown formatted text.

        Returns:
            List of Notion block objects.
        """
        import re

        blocks = []
        lines = markdown_text.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            # Skip empty lines
            if not line.strip():
                i += 1
                continue

            # Heading 2: ## Title
            if line.startswith("## "):
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": line[3:].strip()}}]
                    }
                })
                i += 1
                continue

            # Heading 3: ### Title
            if line.startswith("### "):
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": line[4:].strip()}}]
                    }
                })
                i += 1
                continue

            # Bold heading: **Title**
            bold_match = re.match(r'^\*\*(.+?)\*\*:?\s*$', line.strip())
            if bold_match:
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": bold_match.group(1)}}]
                    }
                })
                i += 1
                continue

            # Bullet list: - item or * item
            if line.strip().startswith(("- ", "* ")):
                content = line.strip()[2:]
                # Parse inline formatting
                rich_text = self._parse_inline_formatting(content)
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": rich_text
                    }
                })
                i += 1
                continue

            # Regular paragraph
            # Parse inline formatting (bold, etc.)
            rich_text = self._parse_inline_formatting(line.strip())
            if rich_text:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": rich_text
                    }
                })
            i += 1

        return blocks

    def _parse_inline_formatting(self, text: str) -> list[dict]:
        """Parse inline Markdown formatting to Notion rich_text.

        Args:
            text: Text with potential **bold** formatting.

        Returns:
            List of rich_text objects.
        """
        import re

        rich_text = []
        # Pattern for **bold** text
        pattern = r'\*\*(.+?)\*\*'

        last_end = 0
        for match in re.finditer(pattern, text):
            # Add text before the match
            if match.start() > last_end:
                plain_text = text[last_end:match.start()]
                if plain_text:
                    rich_text.append({
                        "type": "text",
                        "text": {"content": plain_text}
                    })

            # Add bold text
            rich_text.append({
                "type": "text",
                "text": {"content": match.group(1)},
                "annotations": {"bold": True}
            })
            last_end = match.end()

        # Add remaining text
        if last_end < len(text):
            remaining = text[last_end:]
            if remaining:
                rich_text.append({
                    "type": "text",
                    "text": {"content": remaining}
                })

        # If no formatting found, return plain text
        if not rich_text and text:
            rich_text.append({
                "type": "text",
                "text": {"content": text}
            })

        return rich_text

    def create_deal(self, deal: DealEntry) -> NotionCreateResult:
        """Create a new deal entry in Notion.

        Args:
            deal: DealEntry with deal information.

        Returns:
            NotionCreateResult with creation status.
        """
        import logging
        logger = logging.getLogger(__name__)

        try:
            db_props = self._get_database_properties()
            logger.info(f"Database properties found: {list(db_props.keys())}")

            # Use fallback types if database schema is empty (linked databases)
            use_fallback = len(db_props) == 0
            if use_fallback:
                logger.info("Using fallback property types (database schema not available)")

            properties = {}

            # Map deal fields to Notion properties
            field_values = {
                "title": deal.title,
                "tags": deal.tags,
                "op_source": deal.op_source,
                "external_source": deal.external_source,
                "deck": deal.deck_file_path or deal.deck_url,  # Prefer local file
                "intro": deal.intro,
            }

            for field_key, value in field_values.items():
                prop_name = self.field_mapping.get(field_key)
                if not prop_name:
                    logger.warning(f"No mapping for field: {field_key}")
                    continue

                # Get property config from database or fallback
                if use_fallback:
                    # Use hardcoded fallback types
                    if prop_name in self._fallback_property_types:
                        prop_config = {"type": self._fallback_property_types[prop_name]}
                    else:
                        logger.warning(f"Property '{prop_name}' not in fallback types")
                        continue
                else:
                    if prop_name not in db_props:
                        logger.warning(f"Property '{prop_name}' not found in database. Available: {list(db_props.keys())}")
                        continue
                    prop_config = db_props[prop_name]

                prop_value = self._build_property_value(prop_name, value, prop_config)

                if prop_value:
                    properties[prop_name] = prop_value
                    logger.debug(f"Set property '{prop_name}' = {value}")

            logger.info(f"Creating Notion page with properties: {list(properties.keys())}")

            if not properties:
                return NotionCreateResult(
                    success=False,
                    error="No valid properties to create page. Check field mapping.",
                )

            # Build page body content from detailed_content (Markdown)
            children = []
            if deal.detailed_content:
                children = self._markdown_to_blocks(deal.detailed_content)

            # Create the page with body content
            create_args = {
                "parent": {"database_id": self.database_id},
                "properties": properties,
            }
            if children:
                create_args["children"] = children

            response = self.client.pages.create(**create_args)

            page_id = response.get("id")
            page_url = response.get("url")
            archived = response.get("archived", False)

            logger.info(f"Notion API response - page_id: {page_id}, url: {page_url}, archived: {archived}")
            if children:
                logger.info(f"Added {len(children)} blocks to page body")
            logger.debug(f"Full Notion response: {response}")

            # Validate the response
            if not page_id:
                logger.error(f"Notion returned no page_id. Response: {response}")
                return NotionCreateResult(
                    success=False,
                    error="Notion API returned no page_id",
                )

            # Ensure the URL is properly formatted
            if page_id and not page_url:
                clean_id = page_id.replace("-", "")
                page_url = f"https://www.notion.so/{clean_id}"

            return NotionCreateResult(
                success=True,
                page_id=page_id,
                page_url=page_url,
            )

        except APIResponseError as e:
            logger.error(f"Notion API error: {e.code} - {e.message}")
            return NotionCreateResult(
                success=False,
                error=f"Notion API error: {e.message}",
            )
        except Exception as e:
            logger.exception(f"Failed to create Notion page: {e}")
            return NotionCreateResult(
                success=False,
                error=f"Failed to create Notion page: {str(e)}",
            )

    def create_deal_with_retry(
        self,
        deal: DealEntry,
        max_retries: int = 3,
        initial_delay: float = 1.0,
    ) -> NotionCreateResult:
        """Create a deal with exponential backoff retry.

        Args:
            deal: DealEntry with deal information.
            max_retries: Maximum number of retry attempts.
            initial_delay: Initial delay between retries in seconds.

        Returns:
            NotionCreateResult with creation status.
        """
        import logging
        logger = logging.getLogger(__name__)

        last_error = None
        retryable_errors = [
            "rate_limit",
            "ssl",
            "eof",
            "connection",
            "timeout",
            "temporary",
            "503",
            "502",
            "504",
        ]

        for attempt in range(max_retries):
            result = self.create_deal(deal)

            if result.success:
                return result

            last_error = result.error
            error_lower = (result.error or "").lower()

            # Check if error is retryable
            is_retryable = any(err in error_lower for err in retryable_errors)

            if is_retryable:
                delay = initial_delay * (2 ** attempt)
                logger.warning(f"Notion API error (attempt {attempt + 1}/{max_retries}): {result.error}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                # Non-retryable error
                logger.error(f"Non-retryable Notion error: {result.error}")
                break

        return NotionCreateResult(
            success=False,
            error=last_error or "Max retries exceeded",
        )

    def update_deal(
        self,
        page_id: str,
        updates: dict[str, Any],
    ) -> NotionCreateResult:
        """Update an existing deal entry.

        Args:
            page_id: Notion page ID to update.
            updates: Dictionary of field names to new values.

        Returns:
            NotionCreateResult with update status.
        """
        try:
            db_props = self._get_database_properties()
            properties = {}

            for field_key, value in updates.items():
                prop_name = self.field_mapping.get(field_key)
                if not prop_name or prop_name not in db_props:
                    continue

                prop_config = db_props[prop_name]
                prop_value = self._build_property_value(prop_name, value, prop_config)

                if prop_value:
                    properties[prop_name] = prop_value

            if not properties:
                return NotionCreateResult(
                    success=False,
                    error="No valid properties to update",
                )

            response = self.client.pages.update(
                page_id=page_id,
                properties=properties,
            )

            return NotionCreateResult(
                success=True,
                page_id=response.get("id"),
                page_url=response.get("url"),
            )

        except APIResponseError as e:
            return NotionCreateResult(
                success=False,
                error=f"Notion API error: {e.message}",
            )
        except Exception as e:
            return NotionCreateResult(
                success=False,
                error=f"Failed to update Notion page: {str(e)}",
            )

    def archive_deal(self, page_id: str) -> NotionCreateResult:
        """Archive (delete) a deal entry.

        Args:
            page_id: Notion page ID to archive.

        Returns:
            NotionCreateResult with archive status.
        """
        try:
            self.client.pages.update(
                page_id=page_id,
                archived=True,
            )

            return NotionCreateResult(
                success=True,
                page_id=page_id,
            )

        except APIResponseError as e:
            return NotionCreateResult(
                success=False,
                error=f"Notion API error: {e.message}",
            )
        except Exception as e:
            return NotionCreateResult(
                success=False,
                error=f"Failed to archive Notion page: {str(e)}",
            )

    def check_duplicate(self, company_name: str) -> Optional[dict]:
        """Check if a deal with the same company name already exists.

        Args:
            company_name: Company name to search for.

        Returns:
            Existing page info if found, None otherwise.
        """
        try:
            title_prop = self.field_mapping.get("title", "Title")

            response = self.client.databases.query(
                database_id=self.database_id,
                filter={
                    "property": title_prop,
                    "title": {
                        "equals": company_name,
                    },
                },
            )

            results = response.get("results", [])
            if results:
                return {
                    "page_id": results[0]["id"],
                    "page_url": results[0]["url"],
                }

        except Exception:
            pass

        return None

    def validate_connection(self) -> tuple[bool, str]:
        """Validate that the Notion connection works.

        Returns:
            Tuple of (success, message).
        """
        try:
            db = self.client.databases.retrieve(database_id=self.database_id)
            db_title = ""
            if db.get("title"):
                db_title = db["title"][0]["plain_text"] if db["title"] else ""

            return True, f"Connected to database: {db_title or self.database_id}"

        except APIResponseError as e:
            return False, f"Notion API error: {e.message}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def add_comment(self, page_id: str, text: str) -> bool:
        """Add a comment to a Notion page.

        Args:
            page_id: The Notion page ID.
            text: Comment text content.

        Returns:
            True if successful, False otherwise.
        """
        import logging
        logger = logging.getLogger(__name__)

        try:
            # Notion comments API has a limit of 2000 chars per rich_text block
            # Use conservative limit (1950) to account for Unicode edge cases
            # where Notion may count characters differently than Python len()
            max_chunk_size = 1950
            chunks = []

            remaining = text
            while remaining:
                chunk = remaining[:max_chunk_size]
                remaining = remaining[max_chunk_size:]
                chunks.append({"type": "text", "text": {"content": chunk}})

            self.client.comments.create(
                parent={"page_id": page_id},
                rich_text=chunks,
            )

            logger.info(f"Added comment to page {page_id} ({len(text)} chars)")
            return True

        except APIResponseError as e:
            # APIResponseError uses 'body' or 'code', not 'message'
            error_msg = getattr(e, 'body', None) or getattr(e, 'code', None) or str(e)
            logger.error(f"Failed to add comment: Notion API error: {error_msg}")
            return False
        except Exception as e:
            logger.error(f"Failed to add comment: {e}")
            return False

    def add_comment_multipart(self, page_id: str, text: str, max_per_comment: int = 1900) -> int:
        """Add a long comment as multiple separate comments.

        Splits long text into multiple comments instead of truncating.
        Each comment will have a part indicator like [1/3], [2/3], etc.

        Args:
            page_id: The Notion page ID.
            text: Full comment text content.
            max_per_comment: Max chars per comment (conservative limit).

        Returns:
            Number of comments successfully added.
        """
        import logging
        logger = logging.getLogger(__name__)

        if not text:
            return 0

        # If text fits in one comment, just use add_comment
        if len(text) <= max_per_comment:
            return 1 if self.add_comment(page_id, text) else 0

        # Split into parts
        parts = []
        remaining = text
        while remaining:
            # Leave room for part indicator like " [1/10]" (max ~8 chars)
            chunk_size = max_per_comment - 10
            chunk = remaining[:chunk_size]
            remaining = remaining[chunk_size:]

            # Try to break at a newline or space for cleaner splits
            if remaining and len(chunk) > 100:
                # Look for newline in last 100 chars
                last_newline = chunk.rfind('\n', -100)
                if last_newline > len(chunk) - 150:
                    remaining = chunk[last_newline + 1:] + remaining
                    chunk = chunk[:last_newline + 1]
                else:
                    # Look for space in last 50 chars
                    last_space = chunk.rfind(' ', -50)
                    if last_space > len(chunk) - 80:
                        remaining = chunk[last_space + 1:] + remaining
                        chunk = chunk[:last_space + 1]

            parts.append(chunk)

        total_parts = len(parts)
        success_count = 0

        logger.info(f"Splitting comment into {total_parts} parts for page {page_id}")

        for i, part in enumerate(parts, 1):
            part_text = f"{part.rstrip()}\n\n[{i}/{total_parts}]"
            if self.add_comment(page_id, part_text):
                success_count += 1
            else:
                logger.warning(f"Failed to add comment part {i}/{total_parts}")

        logger.info(f"Added {success_count}/{total_parts} comment parts to page {page_id}")
        return success_count
