"""
tools/bo_crawler.py

Crawls a Back Office web application to generate product_context.md.
Handles Email/Password + TOTP 2FA login automatically.

Usage:
    python tools/bo_crawler.py \
        --url https://bo.example.com \
        --username admin@example.com \
        --password yourpassword \
        --totp-secret JBSWY3DPEHPK3PXP

Output: product_context.md in the project root
"""

import asyncio
import argparse
import json
import re
import sys
import os
from pathlib import Path
from datetime import datetime

import pyotp
from markdownify import markdownify
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.claude_code_client import ClaudeCodeClient

# ── Config ────────────────────────────────────────────────────────────────────

MAX_PAGES     = 40      # max pages to visit
MAX_DEPTH     = 3       # max nav depth to follow
WAIT_MS       = 1500    # ms to wait after navigation
SCREENSHOT_DIR = Path("output/bo_screenshots")

# Selectors likely to contain nav links
NAV_SELECTORS = [
    "nav a", "aside a", "[class*='sidebar'] a", "[class*='menu'] a",
    "[class*='nav'] a", "[role='navigation'] a", "[class*='drawer'] a",
]

# Page segments to skip (not useful for business logic)
SKIP_URL_PATTERNS = [
    "logout", "signout", "sign-out", "log-out",
    "change-password", "profile", "account",
    "#", "javascript:",
]

# ── Claude analysis prompt ────────────────────────────────────────────────────

ANALYSIS_PROMPT = """Output ONLY a markdown section for this Back Office page. Start with ## followed by the page title. No preamble, no tool calls, no file operations.

Document only BUSINESS LOGIC visible on this page:
- What this page/section does (1-2 sentences)
- Key filters, fields, or configurations available
- Business rules in the UI (constraints, validations, statuses, options)
- Actions that can be taken (buttons, workflows, approvals)
- Any roles or permissions mentioned

Skip UI boilerplate (nav labels, pagination, generic buttons).
If the page is a login/error/loading page, output only: SKIP
"""

COMPILE_PROMPT = """Output ONLY a markdown document. Start immediately with "# Product Context" — no preamble, no tool calls, no file operations, no conversational text whatsoever.

Compile the page notes below into a structured reference guide with these sections:

# Product Context

## 1. Product Overview
What the product is and who uses it.

## 2. User Roles & Permissions
All roles and what they can do.

## 3. Key Business Rules
Organised by domain area (deposits, withdrawals, etc.).

## 4. Main Features
How each feature works, key actions available.

## 5. Domain Glossary
Important terms and their meanings.

## 6. Integration Points
Payment gateways, external services, providers.

## 7. Testing Notes
Key test angles and edge cases for each area.

---
PAGE NOTES FROM CRAWL:
{page_notes}
"""

# ── Crawler ───────────────────────────────────────────────────────────────────

class BOCrawler:
    def __init__(self, url: str, username: str, password: str, totp_secret: str = ""):
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.totp = pyotp.TOTP(totp_secret) if totp_secret else None
        self.claude = ClaudeCodeClient()
        self.visited: set[str] = set()
        self.page_notes: list[str] = []
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async def run(self) -> str:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()

            print("\n🔐 Logging in...")
            start_url = await self._login(page)
            print(f"   ✓ Logged in — starting from: {start_url}")

            print("\n🕷️  Starting crawl...")
            # Start from current page after login (not base URL, which may redirect to login)
            await self._crawl(page, start_url, depth=0)

            await browser.close()

        print(f"\n📝 Analysed {len(self.page_notes)} pages. Compiling documentation...")
        doc = await self._compile()
        return doc

    # ── Login ─────────────────────────────────────────────────────────────────

    async def _screenshot(self, page: Page, name: str):
        path = SCREENSHOT_DIR / f"debug_{name}.png"
        await page.screenshot(path=str(path), full_page=False)
        print(f"   📸 Screenshot: {path.name}")

    async def _login(self, page: Page) -> str:
        """Login and return the post-login URL to start crawling from."""
        await page.goto(self.base_url, wait_until="networkidle")
        await self._screenshot(page, "01_login_page")
        print(f"   Page: {await page.title()} | URL: {page.url}")

        # Fill username — try multiple selectors
        filled_user = False
        for sel in [
            'input[type="email"]', 'input[name="username"]', 'input[name="login"]',
            'input[name*="user"]', 'input[name*="email"]',
            'input[placeholder*="email" i]', 'input[placeholder*="user" i]',
            'input[placeholder*="login" i]',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(self.username)
                filled_user = True
                print(f"   Filled username into: {sel}")
                break

        if not filled_user:
            # Last resort: first visible text input
            await page.locator('input[type="text"]').first.fill(self.username)

        # Fill password
        await page.locator('input[type="password"]').first.fill(self.password)
        await self._screenshot(page, "02_filled_credentials")

        # Submit — press Enter on password (most reliable)
        await page.locator('input[type="password"]').first.press("Enter")
        await page.wait_for_timeout(3000)
        await self._screenshot(page, "03_after_submit")
        print(f"   After submit: {await page.title()} | URL: {page.url}")

        # Handle 2FA if prompted
        if self.totp and await self._is_2fa_page(page):
            print("   🔑 2FA dialog detected, entering TOTP...")
            otp = self.totp.now()
            print(f"   OTP: {otp}")

            # OTP may be 6 separate single-digit boxes — click first, type digits one by one
            otp_inputs = page.locator('input[inputmode="numeric"], input[maxlength="1"]')
            n_boxes = await otp_inputs.count()

            if n_boxes >= 6:
                # Individual digit boxes: click first then type each digit
                print(f"   Found {n_boxes} digit boxes, typing digit by digit...")
                await otp_inputs.first.click()
                for digit in otp:
                    await page.keyboard.press(digit)
                    await page.wait_for_timeout(80)
            else:
                # Single OTP input field
                for sel in [
                    'input[inputmode="numeric"]', 'input[autocomplete*="one-time"]',
                    'input[maxlength="6"]', 'input[maxlength="8"]', 'input[type="tel"]',
                ]:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click()
                        await loc.fill(otp)
                        print(f"   Filled OTP into: {sel}")
                        break

            await page.wait_for_timeout(500)

            # Click the Login/Verify button explicitly
            for sel in [
                'button:has-text("Login")', 'button:has-text("Verify")',
                'button:has-text("Confirm")', 'button:has-text("Continue")',
                'button[type="submit"]',
            ]:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click()
                    print(f"   Clicked submit: {sel}")
                    break

            # Wait for navigation away from OTP page
            try:
                await page.wait_for_url(
                    lambda u: "/auth/" not in u,
                    timeout=10000,
                )
            except Exception:
                pass
            await page.wait_for_load_state("networkidle", timeout=8000)
            await page.wait_for_timeout(1000)
            await self._screenshot(page, "04_after_2fa")
            print(f"   After 2FA: {await page.title()} | URL: {page.url}")

        # Verify logged in by checking password field is gone
        pw_fields = await page.locator('input[type="password"]').count()
        if pw_fields > 0:
            await self._screenshot(page, "05_login_failed")
            raise RuntimeError(
                f"Login failed — password field still present on: {page.url}\n"
                f"Page title: {await page.title()}\n"
                "Check credentials. Screenshots saved to output/bo_screenshots/"
            )

        return page.url

    async def _is_2fa_page(self, page: Page) -> bool:
        text = (await page.content()).lower()
        return any(kw in text for kw in [
            "verification code", "authenticator", "two-factor", "2fa",
            "one-time", "otp", "enter code",
        ])

    # ── Crawl ─────────────────────────────────────────────────────────────────

    async def _crawl(self, page: Page, url: str, depth: int):
        if depth > MAX_DEPTH or len(self.visited) >= MAX_PAGES:
            return
        if url in self.visited:
            return
        if any(p in url.lower() for p in SKIP_URL_PATTERNS):
            return

        self.visited.add(url)

        try:
            if page.url != url:
                await page.goto(url, wait_until="networkidle", timeout=15000)
            await page.wait_for_timeout(WAIT_MS)
        except PWTimeout:
            print(f"   ⚠️  Timeout: {url}")
            return
        except Exception as e:
            print(f"   ⚠️  Error navigating {url}: {e}")
            return

        title = await page.title() or url.split("/")[-1] or "Page"
        print(f"   {'  ' * depth}📄 {title} ({url})")

        # Screenshot
        safe_name = re.sub(r"[^\w]", "_", title)[:50]
        await page.screenshot(
            path=str(SCREENSHOT_DIR / f"{len(self.visited):03d}_{safe_name}.png"),
            full_page=False,
        )

        # Extract and analyse page content
        note = await self._analyse_page(page, title)
        if note and note.strip().upper() != "SKIP":
            self.page_notes.append(note)

        # Discover nav links and recurse
        if depth < MAX_DEPTH:
            links = await self._discover_links(page)
            for link in links:
                if len(self.visited) >= MAX_PAGES:
                    break
                await self._crawl(page, link, depth + 1)

    async def _expand_nav(self, page: Page):
        """Click collapsed sidebar section headers to reveal their nav links."""
        # Sidebar section headers (Finance, Wallets, Reports...) are clickable elements
        # in the left area (x < 220, y > 100). We scan all div/li/span elements there.
        try:
            sidebar_els = []
            items = page.locator("div, li, span")
            count = await items.count()
            for i in range(count):
                el = items.nth(i)
                try:
                    box = await el.bounding_box()
                    if not box:
                        continue
                    # Sidebar area: x in [0, 220], y > 100, reasonable size
                    if box["x"] > 220 or box["x"] < 0:
                        continue
                    if box["y"] < 100:
                        continue
                    if box["height"] < 15 or box["height"] > 80:
                        continue
                    if box["width"] < 20 or box["width"] > 280:
                        continue
                    href = await el.get_attribute("href")
                    if href:
                        continue  # skip direct links
                    text = (await el.inner_text()).strip()
                    if len(text) < 2 or "\n" in text:
                        continue
                    sidebar_els.append((text, box["y"], el))
                except Exception:
                    pass

            # De-duplicate by text, sorted by y position
            seen: set[str] = set()
            unique_els = []
            for text, y, el in sorted(sidebar_els, key=lambda t: t[1]):
                if text not in seen:
                    seen.add(text)
                    unique_els.append((text, el))

            clicked = []
            for text, el in unique_els:
                try:
                    await el.click(timeout=1500)
                    await page.wait_for_timeout(500)
                    clicked.append(text)
                except Exception:
                    pass

            if clicked:
                print(f"   🔓 Expanded sidebar: {clicked}")
        except Exception as e:
            print(f"   ⚠️  _expand_nav error: {e}")

        await page.wait_for_timeout(400)

    async def _discover_links(self, page: Page) -> list[str]:
        # Expand collapsed nav sections before collecting links
        await self._expand_nav(page)

        links = set()
        for sel in NAV_SELECTORS:
            try:
                elements = await page.locator(sel).all()
                for el in elements:
                    href = await el.get_attribute("href")
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = self.base_url + href
                    elif not href.startswith("http"):
                        continue
                    if self.base_url in href and href not in self.visited:
                        if not any(p in href.lower() for p in SKIP_URL_PATTERNS):
                            links.add(href)
            except Exception:
                pass
        return list(links)

    # ── Content extraction ────────────────────────────────────────────────────

    async def _analyse_page(self, page: Page, title: str) -> str:
        # Get page text — remove scripts/styles, convert to markdown
        try:
            html = await page.inner_html("body")
        except Exception:
            return ""

        # Strip noise: scripts, styles, SVGs
        html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"<svg[\s\S]*?</svg>", "", html, flags=re.IGNORECASE)

        text = markdownify(html, strip=["a", "img"]).strip()

        # Truncate to avoid massive context
        if len(text) > 6000:
            text = text[:6000] + "\n...[truncated]"

        if len(text) < 100:
            return ""  # Probably empty/loading

        prompt = f"PAGE: {title}\nURL: {page.url}\n\n{text}"

        try:
            result = self.claude.call(ANALYSIS_PROMPT, prompt, timeout=120)
            return result.strip()
        except Exception as e:
            print(f"      ⚠️  Claude analysis failed for '{title}': {e}")
            return f"## {title}\n\n_Analysis failed: {e}_"

    # ── Compile ───────────────────────────────────────────────────────────────

    async def _compile(self) -> str:
        all_notes = "\n\n---\n\n".join(self.page_notes)

        # Truncate if too long for single call
        if len(all_notes) > 80_000:
            all_notes = all_notes[:80_000] + "\n\n...[notes truncated]"

        prompt = COMPILE_PROMPT.format(page_notes=all_notes)

        try:
            doc = self.claude.call(
                "Output plain Markdown text only. No file operations, no tool use, no conversational text. Start immediately with # Product Context.",
                prompt,
                timeout=600,
            )
        except Exception as e:
            doc = "# Product Context\n\n_Compilation failed — raw notes below_\n\n" + all_notes

        # Prepend metadata
        header = (
            f"<!-- Auto-generated by bo_crawler.py on {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n"
            f"<!-- Source: {self.base_url} -->\n\n"
        )
        return header + doc


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crawl a BO and generate product_context.md")
    parser.add_argument("--url",          required=True, help="BO base URL")
    parser.add_argument("--username",     required=True, help="Login username / email")
    parser.add_argument("--password",     required=True, help="Login password")
    parser.add_argument("--totp-secret",  default="",    help="TOTP base32 secret for 2FA")
    parser.add_argument("--output",       default="product_context.md", help="Output file path")
    parser.add_argument("--max-pages",    type=int, default=MAX_PAGES, help="Max pages to crawl")
    args = parser.parse_args()

    crawler = BOCrawler(
        url=args.url,
        username=args.username,
        password=args.password,
        totp_secret=args.totp_secret,
    )

    print(f"\n{'='*60}")
    print(f"🤖 BO Crawler")
    print(f"   Target : {args.url}")
    print(f"   Max    : {args.max_pages} pages")
    print(f"   Output : {args.output}")
    print(f"{'='*60}")

    doc = asyncio.run(crawler.run())

    out_path = Path(args.output)
    out_path.write_text(doc, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"✅ Done! product_context.md written to: {out_path.resolve()}")
    print(f"   Pages crawled  : {len(crawler.visited)}")
    print(f"   Pages analysed : {len(crawler.page_notes)}")
    print(f"   Screenshots    : {SCREENSHOT_DIR}/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
