"""
agents/analyst.py

Analyst Agent — Reads and analyses requirements from a Notion ticket + Figma design.
Output:
  - summary: structured summary of requirements
  - concerns: list of questions / ambiguous points
  - logic_gaps: logical gaps in the design
"""

import json
from tools.claude_code_client import ClaudeCodeClient

SYSTEM_PROMPT = """You are a Senior QA Engineer with 10 years of experience, specialising in requirement analysis and test strategy design.

Your task is to carefully read the provided ticket and Figma design description, then:

1. **Summarise the requirement** in a clear, structured way
2. **Identify concern questions** — ambiguities, gaps, or anything that could cause misunderstanding
3. **Identify logic gaps** — unhandled edge cases, missing screens, or flows that have not been designed

When analysing, consider these angles:
- User perspective: what will the user do, when, and why? (consider both admin/BO and player/MP views)
- Edge cases: what happens with empty data, network errors, permission denied?
- Business rules: are all rules complete and consistent? Cross-check BO config against what MP shows to players.
- UI/UX consistency: does the Figma match the written requirement?
- Integration points: APIs, dependencies on other features?
- Cross-system consistency: does the BO admin action correctly reflect on the MP player side and vice versa?

Your response MUST be valid JSON only (no text outside JSON):
{
  "summary": {
    "feature_name": "string",
    "objective": "string — main objective of the feature",
    "actors": ["string — relevant user roles"],
    "main_flows": ["string — main user flows"],
    "acceptance_criteria": ["string — clear, testable acceptance criteria"]
  },
  "concerns": [
    {
      "area": "string — e.g. Validation, Error handling, Business rule",
      "question": "string — specific question that needs clarification",
      "impact": "High | Medium | Low",
      "suggestion": "string — suggestion if any"
    }
  ],
  "logic_gaps": [
    {
      "scenario": "string — description of the overlooked situation",
      "missing": "string — what is missing",
      "severity": "Critical | Major | Minor"
    }
  ],
  "figma_discrepancies": [
    {
      "description": "string — conflict between Figma and written requirement"
    }
  ]
}"""


USER_PROMPT_TEMPLATE = """Here is the ticket to analyse:

## TICKET

**Title:** {title}

**Properties:**
{properties}

**Content:**
{body_text}

---

## FIGMA DESIGN

{figma_description}

---

## PRODUCT CONTEXT (background knowledge — may include Back Office admin view AND Member Portal player view)

{product_context}

---

## RELATED TICKETS (for cross-feature understanding)

{related_context}

---

## SOURCE CODE (actual implementation — use this to understand real logic, field names, validation rules)

{code_context}

---

Analyse thoroughly and return JSON in the exact format specified above.
When source code is provided, use it to:
- Reference real field names, API endpoints, and data types
- Identify validation already handled in code vs what's missing
- Spot gaps between requirement and actual implementation"""


class AnalystAgent:
    def __init__(self):
        self.client = ClaudeCodeClient()

    async def analyze(
        self,
        ticket: dict,
        figma_description: str = "",
        product_context: str = "",
        related_context: str = "",
        code_context: str = "",
    ) -> dict:
        """
        Analyse a ticket and Figma design.

        Args:
            ticket: dict from NotionClient.get_ticket()
            figma_description: string from FigmaClient.describe_file()

        Returns:
            dict with keys: summary, concerns, logic_gaps, figma_discrepancies
        """
        # Format properties into text
        props_text = "\n".join(
            f"- **{k}**: {v}" for k, v in ticket.get("properties", {}).items()
        ) or "(no properties)"

        figma_text = figma_description or "(No Figma design attached)"

        user_message = USER_PROMPT_TEMPLATE.format(
            title=ticket.get("title", "Untitled"),
            properties=props_text,
            body_text=ticket.get("body_text", "(No content)"),
            figma_description=figma_text,
            product_context=product_context or "(No product context provided)",
            related_context=related_context or "(No related tickets provided)",
            code_context=code_context or "(No source code provided)",
        )

        print("🔍 Analyst Agent analysing requirement...")

        result = self.client.call_json(SYSTEM_PROMPT, user_message)

        summary = result.get("summary", {})
        concerns = result.get("concerns", [])
        gaps = result.get("logic_gaps", [])
        print(f"   ✓ Feature: {summary.get('feature_name', 'N/A')}")
        print(f"   ✓ {len(concerns)} concerns found")
        print(f"   ✓ {len(gaps)} logic gaps found")

        return result

    def format_concerns_for_notion(self, analysis: dict) -> list[str]:
        """
        Convert analysis results into a list of strings to write as a Notion comment.
        """
        lines = []

        # Concerns
        concerns = analysis.get("concerns", [])
        if concerns:
            lines.append("=== CONCERN QUESTIONS ===")
            for c in concerns:
                impact_icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(c.get("impact", ""), "⚪")
                lines.append(
                    f"{impact_icon} [{c.get('area', '')}] {c.get('question', '')}"
                )
                if c.get("suggestion"):
                    lines.append(f"   💡 Suggestion: {c['suggestion']}")

        # Logic gaps
        gaps = analysis.get("logic_gaps", [])
        if gaps:
            lines.append("")
            lines.append("=== LOGIC GAPS ===")
            for g in gaps:
                sev_icon = {"Critical": "🚨", "Major": "⚠️", "Minor": "📝"}.get(g.get("severity", ""), "📝")
                lines.append(
                    f"{sev_icon} [{g.get('severity', '')}] {g.get('scenario', '')}"
                )
                lines.append(f"   Missing: {g.get('missing', '')}")

        # Figma discrepancies
        discrepancies = analysis.get("figma_discrepancies", [])
        if discrepancies:
            lines.append("")
            lines.append("=== FIGMA vs REQUIREMENT ===")
            for d in discrepancies:
                lines.append(f"🎨 {d.get('description', '')}")

        return lines
