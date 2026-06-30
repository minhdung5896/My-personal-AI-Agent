"""
agents/orchestrator.py

Orchestrator — Coordinates the entire pipeline:
  1. Read ticket from Notion
  2. Read Figma design (if available)
  3. Run Analyst Agent
  4. Run Test Designer Agent
  5. Run Script Writer Agent
  6. Write results to Notion + save files

Usage:
    orchestrator = Orchestrator()
    result = await orchestrator.run(page_id="abc123")
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime

from config.settings import OUTPUT_DIR, PLAYWRIGHT_REPO_PATH
from mcp_servers.notion_client import NotionClient
from mcp_servers.figma_client import FigmaClient
from agents.analyst import AnalystAgent
from agents.test_designer import TestDesignerAgent
from tools.html_reporter import save_html_report
from tools.github_reader import GitHubReader, extract_keywords
# from agents.script_writer import ScriptWriterAgent  # temporarily disabled
# from tools.repo_reader import RepoReader            # temporarily disabled


class Orchestrator:
    def __init__(self):
        try:
            self.notion = NotionClient()
        except ValueError:
            self.notion = None
        self.figma = FigmaClient()
        self.analyst = AnalystAgent()
        self.test_designer = TestDesignerAgent()
        self.github = GitHubReader()
        # self.script_writer = ScriptWriterAgent()  # temporarily disabled
        # self.repo_reader = RepoReader()           # temporarily disabled

    # Map environment name → (bo_file, mp_file, bo_label, mp_label)
    ENV_CONTEXT_MAP = {
        "tth1": {
            "bo": "product_context.md",
            "mp": "mp_context.md",
            "bo_label": "BACK OFFICE (TTH1) CONTEXT",
            "mp_label": "MEMBER PORTAL (TTH1) CONTEXT",
        },
        "tph1": {
            "bo": "tph1_product_context.md",
            "mp": "tph1_context.md",
            "bo_label": "BACK OFFICE (TPH1) CONTEXT",
            "mp_label": "MEMBER PORTAL (TPH1) CONTEXT",
        },
    }

    def _load_product_context(self, env: str | None = None) -> str:
        """Load BO + MP context files and combine them.

        Args:
            env: environment name from ENV_CONTEXT_MAP (e.g. "tth1", "tph1").
                 When given, loads only the paired BO+MP files for that env.
                 When None, loads all *_product_context.md and *_context.md files.
        """
        parts = []

        if env and env in self.ENV_CONTEXT_MAP:
            # Paired mode: load only the two files for this environment
            cfg = self.ENV_CONTEXT_MAP[env]
            for file_key, label_key, icon in [
                ("bo", "bo_label", "📚"),
                ("mp", "mp_label", "📱"),
            ]:
                p = Path(cfg[file_key])
                if not p.exists():
                    print(f"   ⚠️  {p.name} not found — skipping")
                    continue
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"# {cfg[label_key]}\n\n" + content)
                    print(f"   {icon} {p.name} ({env.upper()}) loaded")
        else:
            # Auto mode: load all BO and MP context files found in project root
            if env and env not in self.ENV_CONTEXT_MAP:
                print(f"   ⚠️  Unknown env '{env}' — loading all context files")

            bo_files = [Path("product_context.md")] + sorted(Path(".").glob("*_product_context.md"))
            for bo_path in bo_files:
                if not bo_path.exists():
                    continue
                content = bo_path.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                if bo_path.name == "product_context.md":
                    label = "BACK OFFICE (BO) CONTEXT"
                else:
                    stem = bo_path.stem.replace("_product_context", "").upper()
                    label = f"BACK OFFICE ({stem}) CONTEXT"
                parts.append(f"# {label}\n\n" + content)
                print(f"   📚 {bo_path.name} (BO) loaded")

            for mp_path in sorted(Path(".").glob("*_context.md")):
                if mp_path.name == "product_context.md" or mp_path.name.endswith("_product_context.md"):
                    continue
                content = mp_path.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                stem = mp_path.stem.replace("_context", "").upper()
                label = f"MEMBER PORTAL ({stem}) CONTEXT" if stem else "MEMBER PORTAL CONTEXT"
                parts.append(f"# {label}\n\n" + content)
                print(f"   📱 {mp_path.name} (MP) loaded")

        return "\n\n---\n\n".join(parts)

    def _load_feedback_context(self) -> str:
        """Load accumulated team feedback as additional context for Claude."""
        p = Path("feedback_context.md")
        if not p.exists():
            return ""
        content = p.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        print("   💬 feedback_context.md loaded")
        return f"# ACCUMULATED QA FEEDBACK & LEARNINGS\n\n{content}"

    def _load_context_files(self, context_files: list[str]) -> str:
        """Read and concatenate additional context files passed via --context."""
        if not context_files:
            return ""
        parts = []
        for cf in context_files:
            p = Path(cf)
            if p.exists():
                parts.append(f"### {p.name}\n\n{p.read_text(encoding='utf-8').strip()}")
                print(f"   📎 Context file loaded: {p.name}")
            else:
                print(f"   ⚠️  Context file not found: {cf}")
        return "\n\n---\n\n".join(parts)

    async def run_from_file(
        self,
        file_path: str,
        save_scripts: bool = True,
        context_files: list[str] | None = None,
        env: str | None = None,
    ) -> dict:
        """
        Run the full pipeline from a local requirement file (.md / .txt).
        No NOTION_TOKEN needed — results saved to output/.
        """
        from mcp_servers.file_client import FileClient
        file_client = FileClient()

        start_time = datetime.now()
        print(f"\n{'='*60}")
        print(f"🚀 AI Tester Agent starting — processing file: {file_path}")
        print(f"{'='*60}\n")

        print("📋 [1/5] Reading requirement file...")
        ticket = await file_client.get_ticket(file_path)
        print(f"   ✓ Ticket: {ticket['title']}")
        print(f"   ✓ Figma URLs found: {len(ticket['figma_urls'])}")

        print("\n🎨 [2/5] Reading Figma design...")
        figma_description = ""
        if ticket["figma_urls"]:
            print(f"   Found {len(ticket['figma_urls'])} Figma URL(s), fetching specific nodes...")
            figma_description = await self.figma.describe_urls(ticket["figma_urls"])
            print(f"   ✓ {figma_description.count(chr(10))} lines retrieved from Figma")
        else:
            print("   ⚠️  No Figma URLs found, skipping")

        print("\n📚 Loading context...")
        product_context = self._load_product_context(env=env)
        feedback_context = self._load_feedback_context()
        extra_files = self._load_context_files(context_files or [])
        related_context = "\n\n---\n\n".join(filter(None, [feedback_context, extra_files]))

        code_context = ""
        if self.github.configured:
            print("\n💻 [2.5/5] Reading source code from GitHub...")
            keywords = extract_keywords(ticket["title"], ticket.get("body_text", ""))
            code_context = await self.github.get_code_context(keywords)
        else:
            print("   ℹ️  GITHUB_TOKEN / GITHUB_REPO not set — skipping code analysis")

        print("\n🔍 [3/5] Analyst Agent analysing requirement...")
        analysis = await self.analyst.analyze(
            ticket,
            figma_description,
            product_context=product_context,
            related_context=related_context,
            code_context=code_context,
        )

        print("\n📝 [4/5] Test Designer Agent designing test suite...")
        test_suite = await self.test_designer.design_tests(analysis)

        # ── STEP 5 (TEMPORARILY DISABLED): Script Writer Agent ────────────────────────
        # print("\n⚙️  [5/5] Script Writer Agent generate Playwright scripts...")
        # repo_context = self.repo_reader.read_patterns()
        # feature_name = analysis.get("summary", {}).get("feature_name", ticket["title"])
        # scripts = await self.script_writer.generate_scripts(
        #     test_suite=test_suite, repo_context=repo_context, feature_name=feature_name,
        # )
        # saved_files = []
        # if save_scripts:
        #     safe_name = "".join(c if c.isalnum() else "_" for c in feature_name).lower()
        #     saved_files = self.script_writer.save_scripts(scripts, output_subdir=safe_name)
        # ─────────────────────────────────────────────────────────────────

        safe_name = "".join(c if c.isalnum() else "_" for c in ticket["title"]).lower()
        html_path = OUTPUT_DIR / f"{safe_name}.html"
        output_file = save_html_report(
            ticket_title=ticket["title"],
            analysis=analysis,
            test_suite=test_suite,
            output_path=html_path,
        )

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n{'='*60}")
        print(f"✅ Completed in {elapsed:.1f}s")
        print(f"   📊 {len(analysis.get('concerns', []))} concerns")
        print(f"   📊 {len(analysis.get('logic_gaps', []))} logic gaps")
        print(f"   📊 {test_suite.get('coverage_summary', {}).get('total', 0)} test cases")
        print(f"   💾 HTML report: {output_file}")
        print(f"{'='*60}\n")

        return {
            "page_id": ticket["page_id"],
            "ticket_title": ticket["title"],
            "analysis": analysis,
            "test_suite": test_suite,
            "output_file": str(output_file),
            "elapsed_seconds": elapsed,
        }

    async def run(
        self,
        page_id: str,
        write_to_notion: bool = True,
        save_scripts: bool = True,
        env: str | None = None,
    ) -> dict:
        """
        Run the full pipeline for a single Notion ticket.

        Args:
            page_id: Notion page ID (taken from URL after the last /)
            write_to_notion: Whether to write concerns + test cases back to Notion
            save_scripts: Whether to save .spec.ts files to disk

        Returns:
            dict containing the full pipeline results
        """
        start_time = datetime.now()
        print(f"\n{'='*60}")
        print(f"🚀 AI Tester Agent starting — processing ticket: {page_id}")
        print(f"{'='*60}\n")

        # ── STEP 1: Read Notion ticket ──────────────────────────────────
        print("📋 [1/5] Reading Notion ticket...")
        ticket = await self.notion.get_ticket(page_id)
        print(f"   ✓ Ticket: {ticket['title']}")
        print(f"   ✓ Figma URLs found: {len(ticket['figma_urls'])}")

        # ── STEP 2: Read Figma design ───────────────────────────────────
        print("\n🎨 [2/5] Reading Figma design...")
        figma_description = ""
        if ticket["figma_urls"]:
            # Only read the first Figma URL (can be extended later)
            figma_url = ticket["figma_urls"][0]
            figma_description = await self.figma.describe_from_url(figma_url)
            lines = figma_description.count("\n")
            print(f"   ✓ Retrieved {lines} lines of description from Figma")
        else:
            print("   ⚠️  No Figma URL in ticket, skipping this step")

        # ── STEP 2.5: Load product context ─────────────────────────────
        print("\n📚 Loading context...")
        product_context = self._load_product_context(env=env)

        # ── STEP 3: Analyst Agent ──────────────────────────────────────
        print("\n🔍 [3/5] Analyst Agent analysing requirement...")
        analysis = await self.analyst.analyze(ticket, figma_description, product_context=product_context)

        # ── STEP 4: Test Designer Agent ────────────────────────────────
        print("\n📝 [4/5] Test Designer Agent designing test suite...")
        test_suite = await self.test_designer.design_tests(analysis)

        # ── STEP 5 (TEMPORARILY DISABLED): Script Writer Agent ────────────────────────
        # print("\n⚙️  [5/5] Script Writer Agent generate Playwright scripts...")
        # repo_context = self.repo_reader.read_patterns()
        # feature_name = analysis.get("summary", {}).get("feature_name", "Feature")
        # scripts = await self.script_writer.generate_scripts(
        #     test_suite=test_suite, repo_context=repo_context, feature_name=feature_name,
        # )
        # ─────────────────────────────────────────────────────────────────

        # ── OUTPUT: Write to Notion ─────────────────────────────────────
        if write_to_notion:
            print("\n📤 Writing results to Notion...")
            try:
                # Write concern questions
                concern_lines = self.analyst.format_concerns_for_notion(analysis)
                if concern_lines:
                    await self.notion.write_concern_comment(page_id, concern_lines)

                # Write test cases
                await self.notion.write_test_cases(page_id, test_suite)
                print("   ✓ Successfully written to Notion")
            except Exception as e:
                print(f"   ⚠️  Error writing to Notion: {e}")

        # ── OUTPUT: Save local files (script writer temporarily disabled) ───────────
        # saved_files = []
        # if save_scripts:
        #     safe_name = "".join(c if c.isalnum() else "_" for c in feature_name).lower()
        #     saved_files = self.script_writer.save_scripts(scripts, output_subdir=safe_name)

        # Save HTML report
        safe_name = "".join(c if c.isalnum() else "_" for c in ticket["title"]).lower()
        html_path = OUTPUT_DIR / f"{safe_name}.html"
        output_file = save_html_report(
            ticket_title=ticket["title"],
            analysis=analysis,
            test_suite=test_suite,
            output_path=html_path,
        )

        # ── SUMMARY ───────────────────────────────────────────────────
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n{'='*60}")
        print(f"✅ Completed in {elapsed:.1f}s")
        print(f"   📊 {len(analysis.get('concerns', []))} concerns")
        print(f"   📊 {len(analysis.get('logic_gaps', []))} logic gaps")
        print(f"   📊 {test_suite.get('coverage_summary', {}).get('total', 0)} test cases")
        print(f"   💾 HTML report: {output_file}")
        print(f"{'='*60}\n")

        return {
            "page_id": page_id,
            "ticket_title": ticket["title"],
            "analysis": analysis,
            "test_suite": test_suite,
            "output_file": str(output_file),
            "elapsed_seconds": elapsed,
        }

    async def run_batch(
        self,
        database_id: str,
        status_filter: str = "Ready for QA",
        max_tickets: int = 5,
        env: str | None = None,
    ) -> list[dict]:
        """
        Run the pipeline for multiple tickets from a Notion Database.

        Args:
            database_id: Notion Database ID
            status_filter: Filter by Status property
            max_tickets: Maximum number of tickets to process per run
        """
        print(f"📋 Fetching ticket list from database (status: {status_filter})...")
        tickets = await self.notion.get_tickets_from_database(
            database_id, filter_status=status_filter
        )
        tickets = tickets[:max_tickets]
        print(f"   ✓ Found {len(tickets)} tickets")

        results = []
        for i, t in enumerate(tickets, 1):
            print(f"\n[{i}/{len(tickets)}] Processing: {t['title']}")
            try:
                result = await self.run(t["page_id"], env=env)
                results.append(result)
                # Small delay to avoid rate limiting
                if i < len(tickets):
                    await asyncio.sleep(2)
            except Exception as e:
                print(f"   ❌ Error processing ticket {t['page_id']}: {e}")
                results.append({"page_id": t["page_id"], "error": str(e)})

        return results

    def _save_full_output(self, page_id: str, **data) -> Path:
        """Save full output to a JSON file for debugging/auditing."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"output_{page_id[:8]}_{timestamp}.json"
        output_path = OUTPUT_DIR / filename

        serializable = {k: v for k, v in data.items()}
        output_path.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path
