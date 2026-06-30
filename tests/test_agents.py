"""
tests/test_agents.py

Unit tests for each agent — runs with mock data (no real API keys required).
Run: python -m pytest tests/ -v
"""

import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock env before importing
os.environ.setdefault("NOTION_TOKEN", "test-token")
os.environ.setdefault("FIGMA_TOKEN", "test-figma")


# ─── Mock data ────────────────────────────────────────────────────────────────

SAMPLE_TICKET = {
    "page_id": "abc123",
    "title": "Login Feature — Email & Password",
    "properties": {
        "Status": "Ready for QA",
        "Priority": "High",
        "Assignee": ["QA Team"],
    },
    "body_text": """
## Objective
Users can log in using email and password.

## Requirements
- Form includes: Email input, Password input, Remember me checkbox, Login button
- Email must be valid (must contain @)
- Password minimum 8 characters
- If credentials are wrong → display "Incorrect email or password"
- Successful login → redirect to Dashboard
- After 5 failed attempts → lock account for 30 minutes

## Out of scope
- Social login (will be done in the next sprint)
""",
    "figma_urls": [],
}

SAMPLE_FIGMA = """
# Figma Design: Login Flow

## Page: Authentication

### Frame: Login Screen
  Size: 375×812px
  UI Components:
    • Text: "Login"
    • Component: EmailInput — placeholder: "Enter email"
    • Component: PasswordInput — placeholder: "Enter password"
    • Component: RememberMeCheckbox
    • Component: LoginButton — "Login"
    • Text: "Forgot password?"
  Navigation flow:
    → On TAP LoginButton → navigate to Dashboard (PUSH)
    → On TAP ForgotPassword → navigate to ForgotPassword (PUSH)

### Frame: Error State
  UI Components:
    • Component: ErrorBanner — "Incorrect email or password"
"""

SAMPLE_ANALYSIS = {
    "summary": {
        "feature_name": "Login Feature",
        "objective": "Users log in using email and password",
        "actors": ["Unauthenticated user"],
        "main_flows": [
            "User enters email + password → clicks Login → redirected to Dashboard",
            "User enters wrong credentials → error message displayed",
            "User enters wrong credentials 5 times → account locked for 30 minutes",
        ],
        "acceptance_criteria": [
            "Form contains: email, password, remember me, login button",
            "Email validation: must contain @",
            "Password: minimum 8 characters",
            "Wrong credentials → error displayed",
            "5 failed attempts → locked for 30 minutes",
            "Successful login → redirect to Dashboard",
        ],
    },
    "concerns": [
        {
            "area": "Account lockout",
            "question": "After the account is locked for 30 minutes, does the user receive a notification email?",
            "impact": "Medium",
            "suggestion": "An email notification would improve UX",
        },
        {
            "area": "Remember me",
            "question": "How long does Remember me store the session? Does it expire?",
            "impact": "High",
            "suggestion": "Session duration needs to be defined: 7 days, 30 days?",
        },
    ],
    "logic_gaps": [
        {
            "scenario": "User account has not verified email",
            "missing": "No UI/flow to handle this case yet",
            "severity": "Major",
        }
    ],
    "figma_discrepancies": [],
}


# ─── Test functions ───────────────────────────────────────────────────────────

def test_analyst_format_concerns():
    """Test AnalystAgent.format_concerns_for_notion()"""
    # Lazy import to avoid needing a real API key
    from agents.analyst import AnalystAgent

    # Monkey-patch to skip Anthropic client initialization
    agent = object.__new__(AnalystAgent)

    lines = agent.format_concerns_for_notion(SAMPLE_ANALYSIS)
    assert len(lines) > 0, "Must have at least 1 concern line"
    assert any("CONCERN" in l for l in lines), "Must have a CONCERN header"
    assert any("account" in l.lower() for l in lines), "Must have a concern about account lockout"
    print("✅ test_analyst_format_concerns PASSED")


def test_figma_extract_file_key():
    """Test FigmaClient._extract_file_key()"""
    from mcp_servers.figma_client import FigmaClient
    client = object.__new__(FigmaClient)

    urls = [
        ("https://www.figma.com/design/AbCdEf123/My-Design", "AbCdEf123"),
        ("https://www.figma.com/file/XyZ456/Another-File?node-id=1", "XyZ456"),
        ("https://www.figma.com/proto/QrS789/Proto", "QrS789"),
        ("https://google.com", None),
    ]
    for url, expected in urls:
        result = client._extract_file_key(url)
        assert result == expected, f"URL {url}: expected {expected}, got {result}"
    print("✅ test_figma_extract_file_key PASSED")


def test_test_designer_coverage_calc():
    """Test TestDesignerAgent._calculate_coverage()"""
    from agents.test_designer import TestDesignerAgent
    agent = object.__new__(TestDesignerAgent)

    test_cases = [
        {"priority": "High", "type": "functional"},
        {"priority": "High", "type": "validation"},
        {"priority": "Medium", "type": "functional"},
        {"priority": "Low", "type": "ui"},
    ]
    coverage = agent._calculate_coverage(test_cases)
    assert coverage["total"] == 4
    assert coverage["high"] == 2
    assert coverage["medium"] == 1
    assert coverage["low"] == 1
    assert coverage["by_type"]["functional"] == 2
    print("✅ test_test_designer_coverage_calc PASSED")


def test_repo_reader_priority():
    """Test RepoReader._get_priority()"""
    from tools.repo_reader import RepoReader
    reader = RepoReader()

    assert reader._get_priority("playwright.config.ts") == 0
    assert reader._get_priority("pages/login-page-object.ts") < reader._get_priority("tests/login.spec.ts")
    assert reader._get_priority("utils/helper.ts") < reader._get_priority("random-file.ts")
    print("✅ test_repo_reader_priority PASSED")


def test_notion_extract_title():
    """Test NotionClient._extract_title()"""
    from mcp_servers.notion_client import NotionClient
    client = object.__new__(NotionClient)
    client.headers = {}

    page_data = {
        "properties": {
            "Name": {
                "type": "title",
                "title": [
                    {"plain_text": "Login Feature"},
                    {"plain_text": " — Email & Password"},
                ]
            }
        }
    }
    title = client._extract_title(page_data)
    assert title == "Login Feature — Email & Password"
    print("✅ test_notion_extract_title PASSED")


def test_notion_extract_body_with_figma():
    """Test NotionClient._extract_body() extracts Figma URLs."""
    from mcp_servers.notion_client import NotionClient
    client = object.__new__(NotionClient)
    client.headers = {}

    blocks = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "plain_text": "See design here",
                        "href": "https://www.figma.com/design/abc123/Login",
                    }
                ]
            }
        },
        {
            "type": "embed",
            "embed": {
                "url": "https://www.figma.com/design/xyz456/Dashboard",
                "rich_text": [],
            }
        }
    ]
    body_text, figma_urls = client._extract_body(blocks)
    assert len(figma_urls) == 2, f"Expected 2 Figma URLs, got {figma_urls}"
    assert "abc123" in figma_urls[0]
    print("✅ test_notion_extract_body_with_figma PASSED")


if __name__ == "__main__":
    print("\n🧪 Running unit tests for AI Tester Agent...\n")
    tests = [
        test_analyst_format_concerns,
        test_figma_extract_file_key,
        test_test_designer_coverage_calc,
        test_repo_reader_priority,
        test_notion_extract_title,
        test_notion_extract_body_with_figma,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"❌ {test_fn.__name__} FAILED: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("🎉 All tests passed!")
    else:
        sys.exit(1)
