"""
tools/repo_reader.py

Reads your Playwright TypeScript repo to learn patterns:
- Page Object structure
- Fixture setup
- Naming convention
- Custom helper/util
"""

import os
from pathlib import Path
from config.settings import PLAYWRIGHT_REPO_PATH


# Extensions to read
INCLUDE_EXTENSIONS = {".ts", ".js", ".json"}

# Files/folders to skip
EXCLUDE_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage", "test-results"}
EXCLUDE_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}

# Important files to read in full
PRIORITY_PATTERNS = [
    "playwright.config",
    "page-object",
    "page_object",
    "fixture",
    "helper",
    "util",
    "base",
    "common",
]

# Read limits
MAX_FILE_SIZE_KB = 50
MAX_TOTAL_CHARS = 40_000  # ~10k tokens


class RepoReader:
    def __init__(self, repo_path: Path | None = None):
        self.repo_path = repo_path or PLAYWRIGHT_REPO_PATH

    def read_patterns(self) -> str:
        """
        Reads important files in the repo and returns context text.
        Priority: config > page objects > fixtures > test examples
        """
        if not self.repo_path.exists():
            return (
                f"[Repo not found at: {self.repo_path}]\n"
                "Please set PLAYWRIGHT_REPO_PATH in .env"
            )

        collected: list[tuple[int, str, str]] = []  # (priority, path, content)

        for file_path in self._iter_ts_files():
            relative = str(file_path.relative_to(self.repo_path))
            content = self._read_file(file_path)
            if not content:
                continue

            priority = self._get_priority(relative)
            collected.append((priority, relative, content))

        # Sort by priority (lower = more important)
        collected.sort(key=lambda x: x[0])

        # Concatenate content within token limit
        result_parts = [
            f"# Playwright Repo Context\n# Path: {self.repo_path}\n"
        ]
        total_chars = 0

        for priority, rel_path, content in collected:
            chunk = f"\n## File: {rel_path}\n```typescript\n{content}\n```\n"
            if total_chars + len(chunk) > MAX_TOTAL_CHARS:
                result_parts.append(
                    f"\n## [Context limit reached, skipping {rel_path} and remaining files]"
                )
                break
            result_parts.append(chunk)
            total_chars += len(chunk)

        return "".join(result_parts)

    def get_config(self) -> str:
        """Reads playwright.config.ts only."""
        for name in ["playwright.config.ts", "playwright.config.js"]:
            config_path = self.repo_path / name
            if config_path.exists():
                content = self._read_file(config_path)
                return f"```typescript\n// {name}\n{content}\n```"
        return "(playwright.config not found)"

    def get_existing_test_example(self) -> str:
        """
        Fetches 1-2 .spec.ts files as examples for the model to learn style from.
        """
        examples = []
        for file_path in self._iter_ts_files():
            if ".spec." in file_path.name and len(examples) < 2:
                content = self._read_file(file_path)
                if content:
                    rel = str(file_path.relative_to(self.repo_path))
                    examples.append(f"## Example: {rel}\n```typescript\n{content}\n```")
        return "\n\n".join(examples) or "(No .spec.ts files found)"

    # ── PRIVATE ───────────────────────────────────────────────────────────

    def _iter_ts_files(self):
        """Walks all .ts files, skipping excluded dirs."""
        for root, dirs, files in os.walk(self.repo_path):
            # Remove excluded directories in-place
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

            for filename in files:
                if filename in EXCLUDE_FILES:
                    continue
                ext = Path(filename).suffix
                if ext not in INCLUDE_EXTENSIONS:
                    continue
                yield Path(root) / filename

    def _read_file(self, path: Path) -> str | None:
        """Reads a file, returns None if too large or on error."""
        try:
            size_kb = path.stat().st_size / 1024
            if size_kb > MAX_FILE_SIZE_KB:
                return None
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

    def _get_priority(self, relative_path: str) -> int:
        """
        Computes priority for sorting (lower = more important, read first).
        """
        path_lower = relative_path.lower()

        # Config files: priority 0
        if "playwright.config" in path_lower:
            return 0

        # Priority patterns: priority 1-5
        for i, pattern in enumerate(PRIORITY_PATTERNS):
            if pattern in path_lower:
                return i + 1

        # Spec files: priority 10
        if ".spec." in path_lower or ".test." in path_lower:
            return 10

        # Everything else: priority 20
        return 20
