"""
mcp-servers/notion_client.py

Client for reading tickets from Notion and writing results (concerns, test cases) back to Notion.
Uses the Notion REST API v1.

Usage:
    client = NotionClient()
    ticket = await client.get_ticket(page_id="abc123")
    await client.write_concern_comment(page_id="abc123", concerns=[...])
    await client.write_test_cases(page_id="abc123", test_cases=[...])
"""

import httpx
from typing import Optional
from config.settings import NOTION_TOKEN


NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"


class NotionClient:
    def __init__(self):
        if not NOTION_TOKEN:
            raise ValueError("NOTION_TOKEN has not been set in .env")
        self.headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    # ── READ ──────────────────────────────────────────────────────────────

    async def get_ticket(self, page_id: str) -> dict:
        """
        Fetch the full content of a single ticket (Notion page).
        Returns a dict containing: title, properties, body_text, figma_urls
        """
        async with httpx.AsyncClient() as client:
            # Fetch page metadata/properties
            page_resp = await client.get(
                f"{BASE_URL}/pages/{page_id}",
                headers=self.headers,
            )
            page_resp.raise_for_status()
            page_data = page_resp.json()

            # Fetch blocks (body content)
            blocks_resp = await client.get(
                f"{BASE_URL}/blocks/{page_id}/children?page_size=100",
                headers=self.headers,
            )
            blocks_resp.raise_for_status()
            blocks_data = blocks_resp.json()

        title = self._extract_title(page_data)
        properties = self._extract_properties(page_data)
        body_text, figma_urls = self._extract_body(blocks_data["results"])

        return {
            "page_id": page_id,
            "title": title,
            "properties": properties,
            "body_text": body_text,
            "figma_urls": figma_urls,
        }

    async def get_tickets_from_database(
        self,
        database_id: str,
        filter_status: Optional[str] = None,
    ) -> list[dict]:
        """
        Fetch a list of tickets from a Notion Database.
        filter_status: e.g. "Ready for QA", "In Review"
        """
        payload: dict = {"page_size": 20}
        if filter_status:
            payload["filter"] = {
                "property": "Status",
                "status": {"equals": filter_status},
            }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{BASE_URL}/databases/{database_id}/query",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        tickets = []
        for page in data.get("results", []):
            tickets.append({
                "page_id": page["id"],
                "title": self._extract_title(page),
                "url": page.get("url", ""),
            })
        return tickets

    # ── WRITE ─────────────────────────────────────────────────────────────

    async def write_concern_comment(self, page_id: str, concerns: list[str]) -> None:
        """
        Write a list of concern questions to a Notion page as a callout block.
        """
        children = [
            # Header callout
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [{"type": "text", "text": {"content": "🤖 AI Tester — Concern Questions"}}],
                    "icon": {"type": "emoji", "emoji": "⚠️"},
                    "color": "yellow_background",
                },
            },
            # Each concern is a bulleted list item
            *[
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": concern}}]
                    },
                }
                for concern in concerns
            ],
            # Divider
            {"object": "block", "type": "divider", "divider": {}},
        ]
        await self._append_blocks(page_id, children)
        print(f"✅ Written {len(concerns)} concerns to Notion page {page_id}")

    async def write_test_cases(self, page_id: str, test_suite: dict) -> None:
        """
        Write test scope + test cases to a Notion page.
        test_suite = {
            "scope": ["Feature A", "Feature B"],
            "test_cases": [
                {"id": "TC-001", "title": "...", "steps": [...], "expected": "...", "priority": "High"},
                ...
            ]
        }
        """
        children = [
            # Header
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "🧪 Test Suite — AI Generated"}}]
                },
            },
            # Test scope
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "Test Scope"}}]
                },
            },
            *[
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": scope}}]
                    },
                }
                for scope in test_suite.get("scope", [])
            ],
            # Test cases header
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "Test Cases"}}]
                },
            },
        ]

        # Each test case is a toggle block
        for tc in test_suite.get("test_cases", []):
            priority_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(tc.get("priority", ""), "⚪")
            steps_text = "\n".join(
                f"{i+1}. {step}" for i, step in enumerate(tc.get("steps", []))
            )
            children.append({
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"{priority_emoji} {tc['id']} — {tc['title']}"
                            },
                            "annotations": {"bold": True},
                        }
                    ],
                    "children": [
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [
                                    {"type": "text", "text": {"content": "Steps:\n"}, "annotations": {"bold": True}},
                                    {"type": "text", "text": {"content": steps_text}},
                                ]
                            },
                        },
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {
                                "rich_text": [
                                    {"type": "text", "text": {"content": "Expected: "}, "annotations": {"bold": True}},
                                    {"type": "text", "text": {"content": tc.get("expected", "")}},
                                ]
                            },
                        },
                    ],
                },
            })

        children.append({"object": "block", "type": "divider", "divider": {}})
        await self._append_blocks(page_id, children)
        print(f"✅ Written {len(test_suite.get('test_cases', []))} test cases to Notion")

    # ── PRIVATE HELPERS ───────────────────────────────────────────────────

    async def _append_blocks(self, page_id: str, children: list) -> None:
        """Append blocks to the end of a page, batched at 100 blocks per request."""
        async with httpx.AsyncClient() as client:
            for i in range(0, len(children), 100):
                batch = children[i:i + 100]
                resp = await client.patch(
                    f"{BASE_URL}/blocks/{page_id}/children",
                    headers=self.headers,
                    json={"children": batch},
                )
                resp.raise_for_status()

    def _extract_title(self, page_data: dict) -> str:
        props = page_data.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                rich = prop.get("title", [])
                return "".join(r.get("plain_text", "") for r in rich)
        return "Untitled"

    def _extract_properties(self, page_data: dict) -> dict:
        """Extract common properties: Status, Assignee, Priority..."""
        result = {}
        for name, prop in page_data.get("properties", {}).items():
            ptype = prop.get("type")
            if ptype == "select" and prop.get("select"):
                result[name] = prop["select"]["name"]
            elif ptype == "multi_select":
                result[name] = [s["name"] for s in prop.get("multi_select", [])]
            elif ptype == "rich_text":
                result[name] = "".join(
                    r.get("plain_text", "") for r in prop.get("rich_text", [])
                )
            elif ptype == "people":
                result[name] = [p.get("name", "") for p in prop.get("people", [])]
            elif ptype == "url":
                result[name] = prop.get("url", "")
        return result

    def _extract_body(self, blocks: list) -> tuple[str, list[str]]:
        """
        Extract body text and a list of Figma URLs from blocks.
        Returns (body_text: str, figma_urls: list[str])
        """
        lines = []
        figma_urls = []

        for block in blocks:
            btype = block.get("type", "")
            content = block.get(btype, {})
            rich = content.get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)

            if btype in ("paragraph", "quote"):
                if text:
                    lines.append(text)
            elif btype in ("heading_1", "heading_2", "heading_3"):
                if text:
                    level = btype[-1]
                    lines.append(f"{'#' * int(level)} {text}")
            elif btype == "bulleted_list_item":
                lines.append(f"• {text}")
            elif btype == "numbered_list_item":
                lines.append(f"- {text}")
            elif btype == "code":
                code_text = "".join(r.get("plain_text", "") for r in content.get("rich_text", []))
                lines.append(f"```\n{code_text}\n```")
            elif btype == "embed":
                url = content.get("url", "")
                if "figma.com" in url:
                    figma_urls.append(url)
                    lines.append(f"[Figma] {url}")
            elif btype == "bookmark":
                url = content.get("url", "")
                if "figma.com" in url:
                    figma_urls.append(url)

            # Check for URLs in rich text
            for r in rich:
                href = r.get("href", "") or ""
                if "figma.com" in href and href not in figma_urls:
                    figma_urls.append(href)

        return "\n".join(lines), figma_urls
