"""
main.py — Entry point of the AI Tester Agent

Usage:
    # Process a specific ticket
    python main.py --page-id abc123def456

    # Analysis only (no Notion write)
    python main.py --page-id abc123def456 --dry-run

    # Batch: process all "Ready for QA" tickets
    python main.py --batch --database-id xyz789 --status "Ready for QA"

    # Interactive mode (enter page ID at runtime)
    python main.py
"""

import asyncio
import argparse
import sys
from config.settings import validate_config, NOTION_DATABASE_ID


async def run_from_file(file_path: str, context_files: list[str] | None = None, env: str | None = None):
    """Process a requirement from a local file."""
    from agents.orchestrator import Orchestrator
    orchestrator = Orchestrator()
    result = await orchestrator.run_from_file(
        file_path=file_path,
        save_scripts=True,
        context_files=context_files,
        env=env,
    )
    return result


async def run_single(page_id: str, dry_run: bool = False, env: str | None = None):
    """Process a single Notion ticket."""
    from agents.orchestrator import Orchestrator
    orchestrator = Orchestrator()
    result = await orchestrator.run(
        page_id=page_id,
        write_to_notion=not dry_run,
        save_scripts=True,
        env=env,
    )
    return result


async def run_batch(database_id: str, status: str, max_tickets: int, env: str | None = None):
    """Process a batch of tickets from a database."""
    from agents.orchestrator import Orchestrator
    orchestrator = Orchestrator()
    results = await orchestrator.run_batch(
        database_id=database_id,
        status_filter=status,
        max_tickets=max_tickets,
        env=env,
    )
    return results


async def interactive_mode():
    """Interactive mode — enter a page ID via terminal."""
    print("\n🤖 AI Manual Tester Agent")
    print("=" * 40)
    print("Enter the Notion Page ID (found in the URL after the last /)")
    print("Example: https://notion.so/abc123def456... → page_id = abc123def456")
    print("Type 'q' to quit\n")

    while True:
        page_id = input("📋 Page ID: ").strip()
        if page_id.lower() in ("q", "quit", "exit"):
            break
        if not page_id:
            continue

        # Normalize: strip hyphens in case the user copied a UUID-formatted ID
        page_id = page_id.replace("-", "")

        dry_run_input = input("🔍 Dry run (no write to Notion)? [y/N]: ").strip().lower()
        dry_run = dry_run_input == "y"

        try:
            await run_single(page_id, dry_run=dry_run)
        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("Double-check the Page ID and make sure the integration has been shared with this page.\n")

        print()


def main():
    parser = argparse.ArgumentParser(
        description="AI Manual Tester Agent — Automatically analyse requirements and generate test cases"
    )
    parser.add_argument("--input-file", help="Path to a requirement file (.md / .txt) — Notion not required")
    parser.add_argument(
        "--context",
        nargs="*",
        metavar="FILE",
        help="Additional context files (related tickets, API docs, etc.) — e.g. --context related1.md related2.md",
    )
    parser.add_argument(
        "--env",
        default=None,
        choices=["tth1", "tph1"],
        help="Environment to load context for: tth1 (BO+MP TTH1) or tph1 (BO+MP TPH1). "
             "Omit to load all available context files.",
    )
    parser.add_argument("--page-id", help="Notion Page ID to process")
    parser.add_argument("--dry-run", action="store_true", help="Do not write results back to Notion")
    parser.add_argument("--batch", action="store_true", help="Batch mode from database")
    parser.add_argument(
        "--database-id",
        default=NOTION_DATABASE_ID,
        help="Notion Database ID (defaults to value from .env)",
    )
    parser.add_argument(
        "--status",
        default="Ready for QA",
        help="Filter tickets by Status (default: 'Ready for QA')",
    )
    parser.add_argument(
        "--max-tickets",
        type=int,
        default=5,
        help="Maximum number of tickets in batch mode (default: 5)",
    )

    args = parser.parse_args()

    # Validate config before running
    # NOTION_TOKEN is only required when using --page-id or --batch
    require_notion = not args.input_file
    try:
        validate_config(require_notion=require_notion)
    except EnvironmentError as e:
        print(f"❌ Configuration error:\n{e}")
        sys.exit(1)

    if args.input_file:
        asyncio.run(run_from_file(args.input_file, context_files=args.context, env=args.env))

    elif args.batch:
        if not args.database_id:
            print("❌ --database-id is required, or set NOTION_DATABASE_ID in .env")
            sys.exit(1)
        asyncio.run(run_batch(args.database_id, args.status, args.max_tickets, env=args.env))

    elif args.page_id:
        page_id = args.page_id.replace("-", "")
        asyncio.run(run_single(page_id, dry_run=args.dry_run, env=args.env))

    else:
        asyncio.run(interactive_mode())


if __name__ == "__main__":
    main()
