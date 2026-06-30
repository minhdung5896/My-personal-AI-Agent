"""
mcp-servers/figma_client.py

Client for reading Figma designs: component tree, flow connections, annotations.
Converts Figma data into text descriptions for use in LLM context.

Usage:
    client = FigmaClient()
    description = await client.describe_file(file_key="abc123")
    # or from a URL:
    description = await client.describe_from_url("https://figma.com/design/abc123/...")
"""

import re
import httpx
from urllib.parse import urlparse, parse_qs
from config.settings import FIGMA_TOKEN

BASE_URL = "https://api.figma.com/v1"


class FigmaClient:
    def __init__(self):
        if not FIGMA_TOKEN:
            print("⚠️  FIGMA_TOKEN is not set — Figma analysis will be skipped")
        self.headers = {"X-Figma-Token": FIGMA_TOKEN} if FIGMA_TOKEN else {}

    # ── PUBLIC ────────────────────────────────────────────────────────────

    async def describe_from_url(self, figma_url: str) -> str:
        """
        Fetch Figma content from a URL.
        If the URL contains a node-id, fetch only that specific node.
        Supports: figma.com/design/KEY/... and figma.com/file/KEY/...
        """
        file_key = self._extract_file_key(figma_url)
        if not file_key:
            return f"[Could not extract file key from URL: {figma_url}]"

        node_id = self._extract_node_id(figma_url)
        if node_id:
            return await self.describe_nodes(file_key, [node_id])
        return await self.describe_file(file_key)

    async def describe_urls(self, figma_urls: list[str]) -> str:
        """Fetch and combine descriptions from multiple Figma URLs."""
        if not figma_urls:
            return ""
        parts = []
        for url in figma_urls:
            desc = await self.describe_from_url(url)
            if desc and not desc.startswith("["):
                parts.append(desc)
        return "\n\n---\n\n".join(parts) if parts else "[No Figma content retrieved]"

    async def describe_nodes(self, file_key: str, node_ids: list[str]) -> str:
        """
        Fetch specific nodes by ID using /files/{key}/nodes endpoint.
        Much more focused than fetching the whole file.
        """
        if not FIGMA_TOKEN:
            return "[Figma: token not configured]"

        # Figma API expects node IDs with ":" separator (e.g. "29137:74526")
        # URLs use "-" separator (e.g. "29137-74526") — convert
        api_ids = ",".join(nid.replace("-", ":") for nid in node_ids)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{BASE_URL}/files/{file_key}/nodes",
                    headers=self.headers,
                    params={"ids": api_ids, "depth": 3},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return f"[Figma API error {e.response.status_code}: {e.response.text[:200]}]"
        except Exception as e:
            return f"[Figma error: {str(e)}]"

        return self._nodes_to_description(data)

    async def describe_file(self, file_key: str) -> str:
        """Fetch the full Figma file structure as text."""
        if not FIGMA_TOKEN:
            return "[Figma: token not configured]"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{BASE_URL}/files/{file_key}",
                    headers=self.headers,
                    params={"depth": 3},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return f"[Figma API error {e.response.status_code}: {e.response.text[:200]}]"
        except Exception as e:
            return f"[Figma error: {str(e)}]"

        return self._file_to_description(data)

    async def get_comments(self, file_key: str) -> list[dict]:
        """Fetch comments/annotations from a Figma file."""
        if not FIGMA_TOKEN:
            return []
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/files/{file_key}/comments",
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            {
                "author": c.get("user", {}).get("handle", ""),
                "message": c.get("message", ""),
                "resolved": c.get("resolved", False),
            }
            for c in data.get("comments", [])
            if not c.get("resolved", False)  # only include unresolved comments
        ]

    # ── PRIVATE ───────────────────────────────────────────────────────────

    def _extract_node_id(self, url: str) -> str | None:
        """Extract node-id query param from a Figma URL (e.g. '29137-74526')."""
        qs = parse_qs(urlparse(url).query)
        values = qs.get("node-id", [])
        return values[0] if values else None

    def _extract_file_key(self, url: str) -> str | None:
        """
        Extract the file key from a Figma URL.
        Example: https://www.figma.com/design/AbCdEf123/My-Design → AbCdEf123
        """
        patterns = [
            r"figma\.com/(?:design|file)/([A-Za-z0-9_-]+)",
            r"figma\.com/proto/([A-Za-z0-9_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _file_to_description(self, data: dict) -> str:
        """
        Convert Figma JSON into a structured text description for LLM consumption.
        """
        doc = data.get("document", {})
        file_name = data.get("name", "Unnamed")
        lines = [
            f"# Figma Design: {file_name}",
            "",
        ]

        # Iterate over pages
        for page in doc.get("children", []):
            page_name = page.get("name", "Page")
            lines.append(f"## Page: {page_name}")

            frames = [c for c in page.get("children", []) if c.get("type") in ("FRAME", "COMPONENT", "SECTION")]
            if not frames:
                lines.append("  (No frames found)")
                continue

            for frame in frames:
                lines.append(f"\n### Frame: {frame.get('name', 'Unnamed')}")
                lines.append(f"  Size: {self._get_size(frame)}")

                # Describe child components
                components = self._extract_components(frame.get("children", []))
                if components:
                    lines.append("  UI Components:")
                    for comp in components:
                        lines.append(f"    • {comp}")

                # Flow connections
                flows = self._extract_flows(frame)
                if flows:
                    lines.append("  Navigation flows:")
                    for flow in flows:
                        lines.append(f"    → {flow}")

            lines.append("")

        return "\n".join(lines)

    def _nodes_to_description(self, data: dict) -> str:
        """Convert /files/{key}/nodes response to text description."""
        nodes = data.get("nodes", {})
        file_name = ""
        lines = []
        for node_id, node_data in nodes.items():
            if node_data is None:
                continue
            doc = node_data.get("document", {})
            if not file_name:
                file_name = data.get("name", doc.get("name", "Figma"))
                lines.append(f"# Figma Design: {file_name}")
                lines.append("")
            lines.append(f"## Frame: {doc.get('name', node_id)}")
            lines.append(f"  Size: {self._get_size(doc)}")
            components = self._extract_components(doc.get("children", []))
            if components:
                lines.append("  UI Components:")
                for comp in components:
                    lines.append(f"    • {comp}")
            flows = self._extract_flows(doc)
            if flows:
                lines.append("  Navigation flows:")
                for flow in flows:
                    lines.append(f"    → {flow}")
            lines.append("")
        return "\n".join(lines) if lines else "[No node content found]"

    def _get_size(self, node: dict) -> str:
        bb = node.get("absoluteBoundingBox", {})
        if bb:
            return f"{int(bb.get('width', 0))}×{int(bb.get('height', 0))}px"
        return "N/A"

    def _extract_components(self, children: list, depth: int = 0) -> list[str]:
        """Recursively extract names of notable UI components."""
        result = []
        indent = "  " * depth

        # Skip nodes that are too small or invisible
        for child in children:
            if not child.get("visible", True):
                continue

            name = child.get("name", "")
            ctype = child.get("type", "")

            # Skip generic names
            if name.lower() in ("rectangle", "vector", "group", "ellipse", "line"):
                continue

            if ctype == "TEXT":
                text_content = child.get("characters", "")[:60]
                if text_content:
                    result.append(f'{indent}Text: "{text_content}"')
            elif ctype in ("COMPONENT", "INSTANCE"):
                result.append(f"{indent}Component: {name}")
                sub = self._extract_components(child.get("children", []), depth + 1)
                result.extend(sub[:5])  # Limit depth
            elif ctype == "FRAME" and name:
                result.append(f"{indent}Frame: {name}")
            elif ctype in ("INPUT", "BUTTON") or any(
                kw in name.lower() for kw in ["button", "input", "field", "modal", "dialog", "menu", "tab", "nav", "header", "footer", "card", "form", "dropdown", "checkbox", "radio", "toggle", "search"]
            ):
                result.append(f"{indent}{ctype or 'Element'}: {name}")

        return result[:20]  # Limit total number of items

    def _extract_flows(self, node: dict) -> list[str]:
        """Extract prototype flow connections."""
        flows = []
        interactions = node.get("interactions", [])
        for interaction in interactions:
            trigger = interaction.get("trigger", {}).get("type", "")
            action = interaction.get("actions", [{}])[0] if interaction.get("actions") else {}
            dest = action.get("destinationId", "")
            nav_type = action.get("navigationType", "")
            if dest:
                flows.append(f"On {trigger} → navigate to frame {dest} ({nav_type})")
        return flows
