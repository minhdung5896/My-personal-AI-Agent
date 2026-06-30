"""
tools/claude_code_client.py

Wrapper for calling the Claude API.

Priority:
  1. ANTHROPIC_API_KEY in env → use Anthropic Python SDK (Claude Code CLI not required)
  2. Fallback → call `claude -p` subprocess (requires Claude Code to be logged in)
"""

import json
import os
import re
import subprocess


# ── SDK path ──────────────────────────────────────────────────────────────────

def _call_via_sdk(system_prompt: str, user_message: str, timeout: int) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "Package 'anthropic' not found. Run: pip install anthropic"
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is empty.")

    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    max_tokens = int(os.environ.get("MAX_TOKENS", "8096"))

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        timeout=timeout,
    )

    return message.content[0].text


# ── CLI path ───────────────────────────────────────────────────────────────────

def _call_via_cli(system_prompt: str, user_message: str, timeout: int) -> str:
    combined = f"{system_prompt}\n\n---\n\n{user_message}"
    try:
        result = subprocess.run(
            ["claude", "-p", combined, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Claude CLI timeout after {timeout}s. "
            "Try shortening the requirement content or reducing requested test cases."
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Command 'claude' not found and ANTHROPIC_API_KEY is not set. "
            "Either set ANTHROPIC_API_KEY in .env, or install Claude Code: "
            "npm install -g @anthropic-ai/claude-code && claude login"
        )

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Claude CLI error (exit {result.returncode}): {err[:500]}")

    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError("Claude CLI returned empty response.")

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if envelope.get("is_error"):
        raise RuntimeError(f"Claude CLI error: {envelope.get('result', 'Unknown error')}")

    return envelope.get("result", "")


# ── Public client ──────────────────────────────────────────────────────────────

class ClaudeCodeClient:
    def call(self, system_prompt: str, user_message: str, timeout: int = 600) -> str:
        """Send prompt to Claude, return response text.

        Uses Anthropic SDK if ANTHROPIC_API_KEY is set, otherwise falls back to CLI.
        """
        if os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return _call_via_sdk(system_prompt, user_message, timeout)
        return _call_via_cli(system_prompt, user_message, timeout)

    def call_json(self, system_prompt: str, user_message: str, timeout: int = 600) -> dict:
        """Call Claude and parse the result as a JSON dict."""
        raw = self.call(system_prompt, user_message, timeout=timeout)
        try:
            return self._extract_json(raw)
        except json.JSONDecodeError as e:
            preview = raw[:800]
            raise RuntimeError(
                f"Cannot parse JSON from Claude output.\n"
                f"JSON error: {e}\n"
                f"Output (first 800 chars):\n{preview}"
            )

    def _extract_json(self, text: str) -> dict:
        stripped = text.strip()

        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        match = re.search(r'```(?:json)?\s*([\s\S]+)```', stripped)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith("{"):
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start:end + 1])
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError("Cannot extract JSON from Claude output", text, 0)
