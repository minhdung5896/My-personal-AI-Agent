"""
config/settings.py
Centralized configuration for the entire system.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
FIGMA_TOKEN = os.getenv("FIGMA_TOKEN", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")    # e.g. "owner/repo"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# ── Model ─────────────────────────────────────────────
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "8096"))

# ── Paths ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / os.getenv("OUTPUT_DIR", "output")
PLAYWRIGHT_REPO_PATH = Path(os.getenv("PLAYWRIGHT_REPO_PATH", "../playwright-tests"))

OUTPUT_DIR.mkdir(exist_ok=True)

# ── Validation ────────────────────────────────────────
def validate_config(require_notion: bool = True):
    missing = []
    if require_notion and not NOTION_TOKEN:
        missing.append("NOTION_TOKEN")
    if missing:
        raise EnvironmentError(
            f"Missing environment variables: {', '.join(missing)}\n"
            "When using --page-id or --batch, NOTION_TOKEN must be set in .env.\n"
            "To run without Notion, use: python main.py --input-file <file.md>"
        )
