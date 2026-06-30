"""
tools/github_reader.py

Reads relevant source files from a private/public GitHub repo via the GitHub REST API.
Injects code context (services, controllers, models, types) into the Analyst prompt
so the agent understands actual implementation logic, not just the requirement text.

Usage:
    reader = GitHubReader(token, repo="owner/repo", branch="main")
    context = await reader.get_code_context(keywords=["email", "kyc", "deposit"])
"""

import re
import base64
import httpx
from config.settings import GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH

_BASE = "https://api.github.com"

# Directories to prioritise (higher score = read first)
_PRIORITY_DIRS = {
    "service": 10, "services": 10,
    "controller": 9, "controllers": 9,
    "handler": 9, "handlers": 9,
    "model": 8, "models": 8,
    "schema": 8, "schemas": 8,
    "type": 7, "types": 7,
    "interface": 7, "interfaces": 7,
    "dto": 7, "dtos": 7,
    "validation": 7, "validator": 7, "validators": 7,
    "entity": 6, "entities": 6,
    "repository": 6, "repositories": 6,
    "route": 5, "routes": 5,
    "api": 5,
    "component": 4, "components": 4,
    "hook": 3, "hooks": 3,
    "util": 2, "utils": 2, "helper": 2, "helpers": 2,
    "config": 1, "constant": 1, "constants": 1,
}

# Path segments that indicate a file is NOT useful for business logic understanding
_SKIP_SEGMENTS = {
    "node_modules", "__pycache__", ".git", "dist", "build", "coverage",
    "migration", "migrations", "seed", "seeds", "fixture", "fixtures",
    "mock", "mocks", "__mocks__", "spec", "specs", ".test.", ".spec.",
    "storybook", ".stories.", "changelog", "license", "readme",
    "yarn.lock", "package-lock.json", ".lock",
}

# Source file extensions to read
_CODE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx",  # JS/TS
    ".py",                          # Python
    ".java", ".kt",                 # JVM
    ".go",                          # Go
    ".rb",                          # Ruby
    ".php",                         # PHP
    ".cs",                          # C#
    ".swift",                       # iOS
}


class GitHubReader:
    def __init__(
        self,
        token: str = "",
        repo: str = "",
        branch: str = "main",
    ):
        self.token = token or GITHUB_TOKEN
        self.repo = repo or GITHUB_REPO
        self.branch = branch or GITHUB_BRANCH
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @property
    def configured(self) -> bool:
        return bool(self.token and self.repo)

    async def get_code_context(
        self,
        keywords: list[str],
        max_files: int = 12,
        max_chars: int = 30_000,
    ) -> str:
        """
        Main entry point. Returns a code context string to inject into the Analyst prompt.
        Steps:
          1. Fetch full file tree from GitHub
          2. Score each file by keyword relevance + directory priority
          3. Read top-N files and return their content
        """
        if not self.configured:
            return ""

        print(f"   🔍 Searching {self.repo} for files related to: {', '.join(keywords[:6])}")

        tree = await self._get_tree()
        if not tree:
            return "[GitHub: could not fetch file tree]"

        ranked = self._rank_files(tree, keywords)
        top_files = ranked[:max_files]

        print(f"   📂 {len(tree)} files in tree → {len(top_files)} selected")

        parts = []
        total = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            for path, score in top_files:
                if total >= max_chars:
                    break
                content = await self._read_file(client, path)
                if not content:
                    continue
                # Truncate very long files to first 4000 chars (keep top-of-file types/classes)
                snippet = content[:4000]
                if len(content) > 4000:
                    snippet += f"\n// ... [{len(content) - 4000} more chars truncated]"
                parts.append(f"### `{path}`\n```\n{snippet}\n```")
                total += len(snippet)

        if not parts:
            return "[GitHub: no relevant files found]"

        header = f"_Source: `{self.repo}` @ `{self.branch}` — {len(parts)} files_\n\n"
        return header + "\n\n".join(parts)

    # ── Private ────────────────────────────────────────────────────────────

    async def _get_tree(self) -> list[str]:
        """Fetch the full recursive file tree. Returns list of file paths."""
        url = f"{_BASE}/repos/{self.repo}/git/trees/{self.branch}?recursive=1"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, headers=self.headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            print(f"   ⚠️  GitHub tree fetch failed: {e}")
            return []

        return [
            item["path"]
            for item in data.get("tree", [])
            if item.get("type") == "blob"
        ]

    def _rank_files(self, paths: list[str], keywords: list[str]) -> list[tuple[str, float]]:
        """Score each file and return sorted (path, score) list."""
        kws = [k.lower() for k in keywords]
        scored = []
        for path in paths:
            score = self._score(path.lower(), kws)
            if score > 0:
                scored.append((path, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _score(self, path: str, keywords: list[str]) -> float:
        # Skip irrelevant files
        for skip in _SKIP_SEGMENTS:
            if skip in path:
                return 0.0

        # Must be a code file
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if ext not in _CODE_EXTS:
            return 0.0

        score = 0.0
        parts = re.split(r"[/\\._\-]", path)

        # Directory priority
        for part in parts[:-1]:  # exclude filename
            score += _PRIORITY_DIRS.get(part, 0)

        # Keyword match in filename (high value)
        filename = parts[-1] if parts else ""
        for kw in keywords:
            if kw in filename:
                score += 5.0
            # Partial match in path
            elif kw in path:
                score += 2.0

        return score

    async def _read_file(self, client: httpx.AsyncClient, path: str) -> str:
        """Fetch and decode a single file's content via GitHub Contents API."""
        url = f"{_BASE}/repos/{self.repo}/contents/{path}?ref={self.branch}"
        try:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            encoded = data.get("content", "")
            return base64.b64decode(encoded).decode("utf-8", errors="replace")
        except Exception:
            return ""


def extract_keywords(ticket_title: str, body_text: str = "") -> list[str]:
    """
    Extract meaningful keywords from the ticket to drive file search.
    Strips common stopwords and short tokens.
    """
    stopwords = {
        "the", "a", "an", "and", "or", "for", "to", "in", "of", "on",
        "at", "by", "is", "are", "was", "be", "with", "that", "this",
        "it", "as", "from", "not", "will", "has", "have", "had",
        "we", "our", "their", "its",
        # common ticket words
        "include", "update", "add", "new", "fix", "change", "implement",
        "allow", "disallow", "before", "after", "when", "if", "should",
    }

    text = f"{ticket_title} {body_text[:500]}"
    tokens = re.findall(r"[a-zA-Z]{3,}", text)
    seen = set()
    keywords = []
    for t in tokens:
        t_lower = t.lower()
        if t_lower not in stopwords and t_lower not in seen:
            keywords.append(t_lower)
            seen.add(t_lower)

    return keywords[:20]  # cap at 20 keywords
