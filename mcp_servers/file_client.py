"""
mcp_servers/file_client.py

Reads requirements from a local file (.md / .txt) instead of Notion.
Returns the same dict structure as NotionClient.get_ticket().
"""

import re
from pathlib import Path


class FileClient:
    """Reads requirements from a local file — no Notion token required."""

    async def get_ticket(self, file_path: str) -> dict:
        """
        Reads a .md or .txt file and returns a dict compatible with NotionClient.

        Format:
            # Feature name

            Requirement description, acceptance criteria...

            Figma: https://www.figma.com/file/...
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = path.read_text(encoding="utf-8")
        title, body_text, figma_urls = self._parse(content)

        return {
            "page_id": path.stem,
            "title": title,
            "properties": {},
            "body_text": body_text,
            "figma_urls": figma_urls,
        }

    def _parse(self, content: str) -> tuple[str, str, list[str]]:
        lines = content.splitlines()
        title = "Untitled"
        body_start = 0

        for i, line in enumerate(lines):
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                body_start = i + 1
                break

        body_text = "\n".join(lines[body_start:]).strip()

        figma_pattern = re.compile(r'https?://(?:www\.)?figma\.com/\S+')
        figma_urls = []
        for line in lines:
            for url in figma_pattern.findall(line):
                url = url.rstrip(".,;)")
                if url not in figma_urls:
                    figma_urls.append(url)

        return title, body_text, figma_urls

    async def write_concern_comment(self, page_id: str, concerns: list[str]) -> None:
        print("\n--- Concern Questions ---")
        for c in concerns:
            print(f"  • {c}")

    async def write_test_cases(self, page_id: str, test_suite: dict) -> None:
        total = len(test_suite.get("test_cases", []))
        print(f"[FileClient] {total} test cases saved to output/")
