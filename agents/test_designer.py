"""
agents/test_designer.py

Test Designer Agent — Builds the test scope and writes detailed test cases
based on the analysis results from the Analyst Agent.

Output:
  - test_scope: list of areas to be tested
  - test_cases: full list of test cases (happy path + negative + edge)
"""

import json
from tools.claude_code_client import ClaudeCodeClient

SYSTEM_PROMPT = """You are a Senior QA Engineer specialising in writing test cases for web applications.

From the analysed requirement, design a complete test suite covering:

**Test case writing principles:**
- Each test case must be independent and runnable standalone
- Preconditions must be explicit (user is logged in, data exists, etc.)
- Steps must be specific and unambiguous ("Click button X" not "Perform the action")
- Expected results must be verifiable — checkable by eye or by code
- Coverage: Happy path, Negative cases, Boundary values, Permission/Role, UI/UX

**Priority:**
- High: Core business flow, blocking issues
- Medium: Important features, partial flows
- Low: Minor UI, nice-to-have

**Test type:**
- functional: business logic test
- ui: interface and layout test
- validation: form validation test
- permission: access control test
- integration: cross-feature interaction test
- performance: basic page load / response time test

Your response MUST be valid JSON only (no text outside JSON):
{
  "test_scope": [
    "string — brief scope description, e.g. Form validation on the user creation page"
  ],
  "test_cases": [
    {
      "id": "TC-001",
      "title": "string — short, descriptive title",
      "type": "functional | ui | validation | permission | integration",
      "priority": "High | Medium | Low",
      "precondition": "string — condition that must be true before the test",
      "steps": [
        "string — step 1",
        "string — step 2"
      ],
      "expected": "string — expected result",
      "notes": "string — additional notes if any (optional)"
    }
  ],
  "coverage_summary": {
    "total": 0,
    "high": 0,
    "medium": 0,
    "low": 0,
    "by_type": {}
  }
}"""


USER_PROMPT_TEMPLATE = """Below is the analysed requirement — use it to design the test suite:

## FEATURE SUMMARY

**Feature:** {feature_name}
**Objective:** {objective}
**Actors:** {actors}

**Main flows:**
{main_flows}

**Acceptance criteria:**
{acceptance_criteria}

---

## CONCERNS TO COVER WHEN TESTING

{concerns_text}

---

## LOGIC GAPS TO COVER

{gaps_text}

---

## FIGMA DISCREPANCIES

{discrepancies_text}

---

Write a complete test suite. Prioritise full coverage of Acceptance Criteria and High-impact concerns.
Suggested count: 15-25 test cases for a medium feature, 25-40 for a large feature."""


class TestDesignerAgent:
    def __init__(self):
        self.client = ClaudeCodeClient()

    async def design_tests(self, analysis: dict) -> dict:
        """
        Design a test suite from the Analyst Agent's output.

        Args:
            analysis: dict from AnalystAgent.analyze()

        Returns:
            dict with test_scope, test_cases, coverage_summary
        """
        summary = analysis.get("summary", {})
        concerns = analysis.get("concerns", [])
        gaps = analysis.get("logic_gaps", [])
        discrepancies = analysis.get("figma_discrepancies", [])

        # Format concerns
        concerns_text = "\n".join(
            f"- [{c.get('impact')}] {c.get('area')}: {c.get('question')}"
            for c in concerns
        ) or "(No concerns)"

        # Format gaps
        gaps_text = "\n".join(
            f"- [{g.get('severity')}] {g.get('scenario')}: {g.get('missing')}"
            for g in gaps
        ) or "(No logic gaps)"

        # Format discrepancies
        discrepancies_text = "\n".join(
            f"- {d.get('description')}" for d in discrepancies
        ) or "(No discrepancies)"

        # Format main flows and acceptance criteria
        main_flows = "\n".join(
            f"{i+1}. {flow}"
            for i, flow in enumerate(summary.get("main_flows", []))
        ) or "(No specific flows defined)"

        acceptance_criteria = "\n".join(
            f"- {ac}" for ac in summary.get("acceptance_criteria", [])
        ) or "(No AC defined)"

        user_message = USER_PROMPT_TEMPLATE.format(
            feature_name=summary.get("feature_name", "N/A"),
            objective=summary.get("objective", "N/A"),
            actors=", ".join(summary.get("actors", [])) or "N/A",
            main_flows=main_flows,
            acceptance_criteria=acceptance_criteria,
            concerns_text=concerns_text,
            gaps_text=gaps_text,
            discrepancies_text=discrepancies_text,
        )

        print("📝 Test Designer Agent writing test cases...")

        result = self.client.call_json(SYSTEM_PROMPT, user_message)

        # Recalculate coverage summary in case the model got it wrong
        test_cases = result.get("test_cases", [])
        result["coverage_summary"] = self._calculate_coverage(test_cases)

        print(f"   ✓ {len(test_cases)} test cases generated")
        print(f"   ✓ {result['coverage_summary']['high']} High / "
              f"{result['coverage_summary']['medium']} Medium / "
              f"{result['coverage_summary']['low']} Low")

        return result

    def _calculate_coverage(self, test_cases: list) -> dict:
        """Calculate coverage statistics."""
        by_type: dict[str, int] = {}
        high = medium = low = 0

        for tc in test_cases:
            p = tc.get("priority", "Medium")
            if p == "High":
                high += 1
            elif p == "Medium":
                medium += 1
            else:
                low += 1

            t = tc.get("type", "functional")
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total": len(test_cases),
            "high": high,
            "medium": medium,
            "low": low,
            "by_type": by_type,
        }

    def to_markdown(self, test_suite: dict) -> str:
        """
        Export the test suite to Markdown format (for saving to file or sharing).
        """
        lines = ["# Test Suite\n"]

        # Test scope
        lines.append("## Test Scope\n")
        for scope in test_suite.get("test_scope", []):
            lines.append(f"- {scope}")

        # Coverage
        cov = test_suite.get("coverage_summary", {})
        lines.append(f"\n**Total:** {cov.get('total', 0)} test cases | "
                     f"🔴 High: {cov.get('high', 0)} | "
                     f"🟡 Medium: {cov.get('medium', 0)} | "
                     f"🟢 Low: {cov.get('low', 0)}\n")

        # Test cases
        lines.append("## Test Cases\n")
        for tc in test_suite.get("test_cases", []):
            priority_icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(tc.get("priority", ""), "⚪")
            lines.append(f"### {priority_icon} {tc['id']} — {tc['title']}")
            lines.append(f"- **Type:** {tc.get('type', 'functional')}")
            lines.append(f"- **Priority:** {tc.get('priority', 'Medium')}")
            lines.append(f"- **Precondition:** {tc.get('precondition', 'N/A')}")
            lines.append("\n**Steps:**")
            for i, step in enumerate(tc.get("steps", []), 1):
                lines.append(f"{i}. {step}")
            lines.append(f"\n**Expected:** {tc.get('expected', '')}")
            if tc.get("notes"):
                lines.append(f"\n> 📝 {tc['notes']}")
            lines.append("")

        return "\n".join(lines)
