"""
tools/mp_crawler.py

Crawls a Member Portal (player-facing gaming site) to generate context markdown.
Supports three login methods:
  - pin      : mobile number + PIN (2-step flow, e.g. tth1.trykz.dev)
  - password : username + password  (single-form, e.g. tph1.trykz.dev)
  - otp      : phone + OTP fetched live from BO All OTPs page
  - auto     : detect from credentials provided (default)

Usage:
    # Phone + PIN (tth1):
    python tools/mp_crawler.py \\
        --url https://www.tth1.trykz.dev/ \\
        --username 0981029121 --pin 1111

    # Username + Password (tph1):
    python tools/mp_crawler.py \\
        --url https://www.tph1.trykz.dev/ \\
        --username andrey9845758 --password Andrey58 \\
        --output tph1_context.md

    # Phone + OTP from BO (tph1):
    python tools/mp_crawler.py \\
        --url https://www.tph1.trykz.dev/ \\
        --phone 09182938112 --login-method otp \\
        --bo-url https://admin.tth1.trykz.dev/ \\
        --bo-username "Andrey58+1" --bo-password "Anhthu@0706" \\
        --bo-totp GA3GENZSGUZTGLJUMY2DALJUMM2DILLBMJTGELLEMI3GGYZYGRSGGNDCGM \\
        --output tph1_context.md
"""

import asyncio
import argparse
import re
import sys
from pathlib import Path
from datetime import datetime

import pyotp
from markdownify import markdownify
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.claude_code_client import ClaudeCodeClient

# ── Config ────────────────────────────────────────────────────────────────────

MAX_PAGES      = 40
MAX_DEPTH      = 3
WAIT_MS        = 1500
SCREENSHOT_DIR = Path("output/mp_screenshots")

SEED_PATHS = [
    "/promotion", "/promotions",
    "/deposit",
    "/withdrawal", "/withdraw",
    "/profile", "/account", "/me",
    "/history", "/transaction", "/transactions",
    "/referral",
    "/vip",
    "/games", "/lobby",
]

SKIP_URL_PATTERNS = [
    "logout", "signout", "sign-out", "log-out",
    "#", "javascript:", "mailto:", "tel:",
    "/game/", "/games/launch", "/play/", "/launch/",
    "/slot/", "/casino/", "/sport/", "/sports/",
]

# ── Prompts ───────────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """Output ONLY a markdown section for this Member Portal page. Start with ## followed by the page title. No preamble, no tool calls, no file operations.

Document only BUSINESS LOGIC visible on this page from a PLAYER perspective:
- What this page/section does (1-2 sentences)
- Key features, fields, or information displayed
- Business rules (limits, conditions, requirements shown to players)
- Actions players can take (deposit, claim, withdraw, etc.)
- Any eligibility conditions or requirements shown
- Important UI patterns (forms, steps, confirmations)

Skip generic UI boilerplate (nav labels, footers, cookie banners).
If the page is a login/error/loading page, output only: SKIP
"""

COMPILE_PROMPT = """Output ONLY a markdown document starting with "# Member Portal Context".
No preamble, no tool calls, no file operations, no conversational text.

Compile the page notes below into a structured reference guide covering the PLAYER experience:

# Member Portal Context

## 1. Platform Overview
What the site is, target audience, available games/features.

## 2. Registration & Authentication
How players sign up, login flows, identity verification.

## 3. Deposit Flow
Available deposit methods, limits, steps, processing info shown to players.

## 4. Withdrawal Flow
How players withdraw, requirements, limits, steps.

## 5. Promotions & Bonuses (Player View)
All promotions visible to players: how to claim, eligibility, terms shown.

## 6. Game Lobby
Available game categories, providers, special features.

## 7. Player Account & Profile
Profile settings, verification, history, preferences.

## 8. Transaction History
How players view their activity, what data is shown.

## 9. VIP / Loyalty Program
Tiers, benefits, how to qualify (if visible).

## 10. Testing Notes
Key test scenarios from a player/QA perspective.

PAGE NOTES FROM CRAWL:
{page_notes}
"""

# ── Crawler ───────────────────────────────────────────────────────────────────

class MPCrawler:
    def __init__(
        self,
        url: str,
        username: str = "",
        pin: str = "",
        password: str = "",
        phone: str = "",
        login_method: str = "auto",
        max_pages: int = MAX_PAGES,
        bo_url: str = "",
        bo_username: str = "",
        bo_password: str = "",
        bo_totp: str = "",
    ):
        self.base_url   = url.rstrip("/")
        self.username   = username
        self.pin        = pin
        self.password   = password
        self.phone      = phone or username  # OTP mode falls back to username
        self.login_method = login_method
        self.max_pages  = max_pages
        self.bo_url      = bo_url.rstrip("/") if bo_url else ""
        self.bo_username = bo_username
        self.bo_password = bo_password
        self.bo_totp     = bo_totp
        self.claude      = ClaudeCodeClient()
        self.visited: set[str] = set()
        self.page_notes: list[str] = []
        self._browser   = None  # set in run() — needed for BO OTP fetching
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async def run(self) -> str:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            self._browser = browser  # store so _fetch_otp_from_bo can open a second context

            ctx = await browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
                ),
            )
            page = await ctx.new_page()

            print("\n🔐 Logging in to MP...")
            start_url = await self._login(page)
            print(f"   ✓ Logged in — starting from: {start_url}")

            seeds = {self.base_url + p for p in SEED_PATHS}

            print("\n🕷️  Starting crawl...")
            await self._crawl(page, start_url, depth=0)

            print("\n🌱 Crawling seed paths...")
            for seed_url in seeds:
                if len(self.visited) >= self.max_pages:
                    break
                if seed_url not in self.visited:
                    await self._crawl(page, seed_url, depth=1)

            await browser.close()

        print(f"\n📝 Analysed {len(self.page_notes)} pages. Compiling documentation...")
        return await self._compile()

    # ── Screenshot ────────────────────────────────────────────────────────────

    async def _screenshot(self, page: Page, name: str):
        path = SCREENSHOT_DIR / f"debug_{name}.png"
        await page.screenshot(path=str(path), full_page=False)
        print(f"   📸 Screenshot: {path.name}")

    # ── Login dispatch ────────────────────────────────────────────────────────

    async def _login(self, page: Page) -> str:
        """Navigate to MP, open login modal, dispatch to the right login method, return post-login URL."""
        await page.goto(self.base_url, wait_until="networkidle")
        await self._screenshot(page, "01_home")
        print(f"   Page: {await page.title()} | URL: {page.url}")

        # Auto-detect method from credentials provided
        method = self.login_method
        if method == "auto":
            if self.password:
                method = "password"
            elif self.pin:
                method = "pin"
            elif self.bo_url:
                method = "otp"
            else:
                method = "pin"
        print(f"   Login method: {method}")

        # Open login modal
        await self._open_login_modal(page)
        await self._screenshot(page, "02_login_modal")

        # Fill credentials
        if method == "pin":
            await self._login_pin(page)
        elif method == "password":
            await self._login_password(page)
        elif method == "otp":
            await self._login_phone_otp(page)
        else:
            raise ValueError(f"Unknown login method: {method}")

        # Submit the form inside the modal
        await self._click_modal_submit(page)

        # Wait for modal to close
        try:
            await page.wait_for_selector(
                '.onboarding-dialog, [class*="onboarding"]',
                state="hidden",
                timeout=10000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        await self._screenshot(page, "06_after_login")
        print(f"   After login: {await page.title()} | URL: {page.url}")

        # Verify: modal + credential form should be gone
        onboarding_visible = await page.locator(
            '.onboarding-dialog:visible, [class*="onboarding"]:visible'
        ).count()
        if onboarding_visible > 0:
            pin_boxes = await page.locator('input[inputmode="numeric"]').count()
            if pin_boxes > 0:
                await self._screenshot(page, "07_login_failed")
                raise RuntimeError(
                    f"Login failed — credential form still visible on: {page.url}\n"
                    f"Check credentials. Screenshots: {SCREENSHOT_DIR}/"
                )

        return page.url

    async def _open_login_modal(self, page: Page):
        """Click the header Login button to open the modal."""
        for sel in [
            'button:has-text("Login")',   'a:has-text("Login")',
            'button:has-text("เข้าสู่ระบบ")', 'a:has-text("เข้าสู่ระบบ")',
            'button:has-text("Sign in")', '[data-testid*="login"]',
            '[class*="login"]',           '[class*="Login"]',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click()
                print(f"   Clicked login button: {sel}")
                await page.wait_for_timeout(1500)
                return

        # Fallback: navigate to a login path directly
        for path in ["/login", "/sign-in", "/auth"]:
            try:
                await page.goto(self.base_url + path, wait_until="networkidle")
                print(f"   Navigated to: {path}")
                return
            except Exception:
                pass

    # ── Login method: PIN (2-step: phone → Next → PIN digit boxes) ────────────

    async def _login_pin(self, page: Page):
        """Fill phone number, click Next, then fill 4-digit PIN boxes."""
        for sel in [
            'input[name="phone"]',       'input[type="tel"]',
            'input[name="mobile"]',      'input[placeholder*="phone" i]',
            'input[placeholder*="mobile" i]', 'input[type="text"]',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(self.username)
                print(f"   Filled phone into: {sel}")
                break

        await self._screenshot(page, "03_step1_phone")

        # Click Next to reveal PIN step
        for sel in ['button:has-text("Next")', 'button:has-text("ถัดไป")', 'button[type="submit"]']:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click()
                print(f"   Clicked Next: {sel}")
                await page.wait_for_timeout(1500)
                break

        await self._screenshot(page, "04_step2_pin_form")

        # Fill PIN — 4 individual digit boxes (tap first, then type digits)
        pin_boxes = page.locator('input[inputmode="numeric"], input[maxlength="1"]')
        n_boxes = await pin_boxes.count()

        if n_boxes >= 4:
            print(f"   Found {n_boxes} PIN digit boxes, typing digit by digit...")
            await pin_boxes.first.click()
            for digit in self.pin:
                await page.keyboard.press(digit)
                await page.wait_for_timeout(80)
        else:
            for sel in [
                'input[type="password"]', 'input[name="pin"]',
                'input[name="PIN"]',      'input[placeholder*="PIN" i]',
                'input[maxlength="4"]',
            ]:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(self.pin)
                    print(f"   Filled PIN into: {sel}")
                    break

        await page.wait_for_timeout(300)
        await self._screenshot(page, "05_filled_pin")

    # ── Login method: Password (single-form: username + password) ─────────────

    async def _login_password(self, page: Page):
        """Fill username and password in a standard single-step form."""
        for sel in [
            '.onboarding-dialog input[name="username"]',
            '[role="dialog"] input[name="username"]',
            'input[name="username"]',
            '.onboarding-dialog input[placeholder="Username"]',
            'input[placeholder="Username"]',
            'input[placeholder*="username" i]',
            'input[placeholder*="user" i]',
            'input[name="login"]',
            'input[type="text"]',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(self.username)
                print(f"   Filled username into: {sel}")
                break

        for sel in [
            '.onboarding-dialog input[type="password"]',
            '[role="dialog"] input[type="password"]',
            'input[type="password"]',
            'input[name="password"]',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(self.password)
                print(f"   Filled password into: {sel}")
                break

        await self._screenshot(page, "03_filled_credentials")

    # ── Login method: Phone + OTP from BO ─────────────────────────────────────

    async def _login_phone_otp(self, page: Page):
        """Switch to phone login tab, enter phone, request OTP, fetch it from BO, enter it."""
        # Switch to phone-login tab
        for sel in [
            'text="Login with Phone Number >"',
            'text="Login with Phone"',
            'a:has-text("Phone")',
            'button:has-text("Phone")',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click()
                print(f"   Switched to phone login: {sel}")
                await page.wait_for_timeout(1000)
                break

        await self._screenshot(page, "03_phone_otp_tab")

        # Enter phone number
        for sel in [
            'input[name="phone"]', 'input[type="tel"]', 'input[name="mobile"]',
            'input[placeholder*="phone" i]', 'input[placeholder*="mobile" i]',
            'input[type="text"]',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(self.phone)
                print(f"   Filled phone into: {sel}")
                break

        # Request OTP from server
        for sel in [
            'button:has-text("Get OTP")',     'button:has-text("Send OTP")',
            'button:has-text("Request OTP")', 'button:has-text("ขอ OTP")',
            'button:has-text("รับ OTP")',     'button:has-text("OTP")',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click()
                print(f"   Clicked Get OTP: {sel}")
                await page.wait_for_timeout(3000)  # let server send OTP
                break

        await self._screenshot(page, "04_otp_requested")

        # Fetch OTP code from BO
        print("   🔎 Fetching OTP from BO All OTPs page...")
        otp_code = await self._fetch_otp_from_bo()
        print(f"   ✓ Got OTP: {otp_code}")

        # Enter OTP — 6 individual digit boxes or a single input
        otp_inputs = page.locator('input[inputmode="numeric"], input[maxlength="1"]')
        n_boxes = await otp_inputs.count()

        if n_boxes >= 6:
            print(f"   Found {n_boxes} OTP digit boxes, typing digit by digit...")
            await otp_inputs.first.click()
            for digit in otp_code:
                await page.keyboard.press(digit)
                await page.wait_for_timeout(80)
        else:
            for sel in [
                'input[maxlength="6"]',        'input[inputmode="numeric"]',
                'input[autocomplete*="one-time"]', 'input[placeholder*="OTP" i]',
                'input[type="number"]',
            ]:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(otp_code)
                    print(f"   Filled OTP into: {sel}")
                    break

        await page.wait_for_timeout(300)
        await self._screenshot(page, "05_filled_otp")

    async def _click_modal_submit(self, page: Page):
        """Click the Login/Submit button inside the open modal."""
        for sel in [
            '.onboarding-dialog button:has-text("Login")',
            '[class*="onboarding"] button:has-text("Login")',
            '[role="dialog"] button:has-text("Login")',
            '.onboarding-dialog button[type="submit"]',
            '[role="dialog"] button[type="submit"]',
            'button:has-text("เข้าสู่ระบบ")',
            'button:has-text("Verify")',
            'button:has-text("Confirm")',
            'button[type="submit"]:visible',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click()
                print(f"   Clicked submit: {sel}")
                return

    # ── BO OTP fetcher ────────────────────────────────────────────────────────

    async def _fetch_otp_from_bo(self) -> str:
        """Open a BO browser context, login, navigate to All OTPs, return the OTP for self.phone."""
        if not self.bo_url or not self.bo_username or not self.bo_password:
            raise RuntimeError(
                "BO credentials required for otp login method. "
                "Provide --bo-url, --bo-username, --bo-password."
            )

        ss_dir = SCREENSHOT_DIR / "bo_otp"
        ss_dir.mkdir(parents=True, exist_ok=True)

        bo_ctx = await self._browser.new_context(viewport={"width": 1440, "height": 900})
        bo_page = await bo_ctx.new_page()

        async def ss(name):
            await bo_page.screenshot(path=str(ss_dir / f"{name}.png"), full_page=False)

        try:
            # ── Login to BO ──────────────────────────────────────────────────
            print(f"   🔓 Logging into BO: {self.bo_url}")
            await bo_page.goto(self.bo_url, wait_until="networkidle")
            await ss("01_bo_login")

            for sel in [
                'input[type="email"]',    'input[name="username"]',
                'input[name="login"]',    'input[placeholder*="email" i]',
                'input[placeholder*="user" i]',
            ]:
                loc = bo_page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(self.bo_username)
                    print(f"   BO username into: {sel}")
                    break

            await bo_page.locator('input[type="password"]').first.fill(self.bo_password)
            await bo_page.locator('input[type="password"]').first.press("Enter")
            await bo_page.wait_for_timeout(3000)
            await ss("02_bo_after_submit")

            # Handle BO 2FA (TOTP)
            if self.bo_totp:
                content = (await bo_page.content()).lower()
                if any(kw in content for kw in ["otp", "verification", "two-factor", "authenticator", "code"]):
                    print("   🔑 BO 2FA detected, entering TOTP...")
                    otp = pyotp.TOTP(self.bo_totp).now()
                    print(f"   TOTP: {otp}")

                    otp_inputs = bo_page.locator('input[inputmode="numeric"], input[maxlength="1"]')
                    n_boxes = await otp_inputs.count()
                    if n_boxes >= 6:
                        await otp_inputs.first.click()
                        for digit in otp:
                            await bo_page.keyboard.press(digit)
                            await bo_page.wait_for_timeout(80)
                    else:
                        for sel in ['input[inputmode="numeric"]', 'input[maxlength="6"]']:
                            loc = bo_page.locator(sel).first
                            if await loc.count() > 0:
                                await loc.fill(otp)
                                break

                    await bo_page.wait_for_timeout(500)
                    for sel in ['button:has-text("Login")', 'button:has-text("Verify")', 'button[type="submit"]']:
                        loc = bo_page.locator(sel).first
                        if await loc.count() > 0:
                            await loc.click()
                            break

                    await bo_page.wait_for_timeout(3000)
                    await ss("03_bo_after_2fa")

            print(f"   BO logged in: {await bo_page.title()} | {bo_page.url}")

            # ── Navigate to All OTPs ─────────────────────────────────────────
            # Try sidebar nav item first
            nav_found = False
            for label in ["All OTPs", "All OTP", "All Otp", "OTP", "All Otps"]:
                loc = bo_page.locator(f'text="{label}"').first
                if await loc.count() > 0:
                    await loc.click()
                    await bo_page.wait_for_timeout(2000)
                    print(f"   Navigated via sidebar: {label}")
                    nav_found = True
                    break

            if not nav_found:
                # Try direct URL patterns
                for path in ["/all-otp", "/all-otps", "/players/all-otp", "/players/all-otps", "/player/otp"]:
                    target = self.bo_url + path
                    await bo_page.goto(target, wait_until="networkidle")
                    await bo_page.wait_for_timeout(1500)
                    body_text = (await bo_page.inner_text("body")).lower()
                    if "otp" in body_text:
                        print(f"   Navigated to All OTPs: {target}")
                        break

            await ss("04_bo_all_otps_page")
            print(f"   All OTPs page: {await bo_page.title()} | {bo_page.url}")

            # Search for the phone number
            for sel in [
                'input[placeholder*="search" i]', 'input[placeholder*="phone" i]',
                'input[placeholder*="mobile" i]', 'input[type="search"]',
                'input[type="text"]',
            ]:
                loc = bo_page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(self.phone)
                    await bo_page.keyboard.press("Enter")
                    await bo_page.wait_for_timeout(2000)
                    print(f"   Searched for phone: {self.phone}")
                    break

            await ss("05_bo_otp_search_results")

            # Extract OTP from table
            otp_code = await self._extract_otp_from_table(bo_page)

            if not otp_code:
                # Refresh and try once more (OTP might not have arrived yet)
                await bo_page.reload(wait_until="networkidle")
                await bo_page.wait_for_timeout(2000)
                otp_code = await self._extract_otp_from_table(bo_page)

            if not otp_code:
                # Last resort: grep the whole page for any 6-digit number
                page_text = await bo_page.inner_text("body")
                await ss("06_bo_otp_not_found")
                matches = re.findall(r'\b\d{6}\b', page_text)
                if matches:
                    otp_code = matches[0]
                    print(f"   Found OTP via regex fallback: {otp_code}")
                else:
                    snippet = page_text[:500]
                    raise RuntimeError(
                        f"Could not find OTP for phone {self.phone} on BO All OTPs page.\n"
                        f"Page text: {snippet}"
                    )

            return otp_code

        finally:
            await bo_ctx.close()

    async def _extract_otp_from_table(self, page: Page) -> str:
        """Find the table row for self.phone and return the 6-digit OTP from it."""
        try:
            phone_rows = page.locator('tr').filter(has=page.locator(f'text="{self.phone}"'))
            if await phone_rows.count() == 0:
                return ""

            row = phone_rows.first
            cells = await row.locator('td').all_inner_texts()
            print(f"   OTP row cells: {cells}")

            for cell in cells:
                cell = cell.strip()
                if re.match(r'^\d{6}$', cell):
                    return cell

            # Fallback: any 6-digit number in the row
            row_text = await row.inner_text()
            matches = re.findall(r'\b\d{6}\b', row_text)
            if matches:
                return matches[0]

        except Exception as e:
            print(f"   ⚠️  OTP table extraction error: {e}")

        return ""

    # ── Crawl ─────────────────────────────────────────────────────────────────

    async def _crawl(self, page: Page, url: str, depth: int):
        if depth > MAX_DEPTH or len(self.visited) >= self.max_pages:
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

        safe_name = re.sub(r"[^\w]", "_", title)[:50]
        await page.screenshot(
            path=str(SCREENSHOT_DIR / f"{len(self.visited):03d}_{safe_name}.png"),
            full_page=False,
        )

        note = await self._analyse_page(page, title)
        if note and note.strip().upper() != "SKIP":
            self.page_notes.append(note)

        if depth < MAX_DEPTH:
            links = await self._discover_links(page)
            for link in links:
                if len(self.visited) >= self.max_pages:
                    break
                await self._crawl(page, link, depth + 1)

    async def _discover_links(self, page: Page) -> list[str]:
        links = set()
        try:
            for el in await page.locator("a[href]").all():
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

        await self._click_nav_items(page, links)
        return list(links)

    async def _click_nav_items(self, page: Page, discovered: set):
        for sel in [
            "[class*='bottom'] a", "[class*='tab'] a", "[class*='nav'] a",
            "[class*='menu'] a", "[class*='footer'] a",
            "header a", "nav a",
            "[class*='promotion'] a", "[class*='deposit'] a", "[class*='withdraw'] a",
        ]:
            try:
                for el in await page.locator(sel).all():
                    href = await el.get_attribute("href")
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = self.base_url + href
                    elif not href.startswith("http"):
                        continue
                    if self.base_url in href and href not in self.visited:
                        if not any(p in href.lower() for p in SKIP_URL_PATTERNS):
                            discovered.add(href)
            except Exception:
                pass

    # ── Content extraction ────────────────────────────────────────────────────

    async def _analyse_page(self, page: Page, title: str) -> str:
        try:
            html = await page.inner_html("body")
        except Exception:
            return ""

        html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"<style[\s\S]*?</style>",   "", html, flags=re.IGNORECASE)
        html = re.sub(r"<svg[\s\S]*?</svg>",       "", html, flags=re.IGNORECASE)

        text = markdownify(html, strip=["a", "img"]).strip()

        if len(text) > 6000:
            text = text[:6000] + "\n...[truncated]"
        if len(text) < 100:
            return ""

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

        if len(all_notes) > 80_000:
            all_notes = all_notes[:80_000] + "\n\n...[notes truncated]"

        prompt = COMPILE_PROMPT.format(page_notes=all_notes)

        try:
            doc = self.claude.call(
                "Output plain Markdown text only. No file operations, no tool use, no conversational text. "
                "Start immediately with # Member Portal Context.",
                prompt,
                timeout=600,
            )
        except Exception as e:
            doc = "# Member Portal Context\n\n_Compilation failed — raw notes below_\n\n" + all_notes

        header = (
            f"<!-- Auto-generated by mp_crawler.py on {datetime.now().strftime('%Y-%m-%d %H:%M')} -->\n"
            f"<!-- Source: {self.base_url} -->\n\n"
        )
        return header + doc


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Crawl a Member Portal and generate a context markdown file.\n\n"
            "Login methods (--login-method):\n"
            "  auto     - auto-detect from provided credentials (default)\n"
            "  pin      - phone number + PIN  (e.g. tth1.trykz.dev)\n"
            "  password - username + password (e.g. tph1.trykz.dev)\n"
            "  otp      - phone + OTP fetched live from BO All OTPs page"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Target
    parser.add_argument("--url",          required=True, help="MP base URL")
    parser.add_argument("--output",       default="mp_context.md", help="Output file path")
    parser.add_argument("--max-pages",    type=int, default=MAX_PAGES, help="Max pages to crawl")
    parser.add_argument("--login-method", default="auto",
                        choices=["auto", "pin", "password", "otp"],
                        help="Login method (default: auto-detect from credentials)")

    # MP credentials
    parser.add_argument("--username", default="", help="Username or mobile number")
    parser.add_argument("--pin",      default="", help="PIN code (for pin method)")
    parser.add_argument("--password", default="", help="Password (for password method)")
    parser.add_argument("--phone",    default="", help="Phone number (for otp method; falls back to --username)")

    # BO credentials for OTP fetching
    parser.add_argument("--bo-url",      default="", help="BO admin URL  (for otp method)")
    parser.add_argument("--bo-username", default="", help="BO username   (for otp method)")
    parser.add_argument("--bo-password", default="", help="BO password   (for otp method)")
    parser.add_argument("--bo-totp",     default="", help="BO TOTP secret (for otp method)")

    args = parser.parse_args()

    crawler = MPCrawler(
        url=args.url,
        username=args.username,
        pin=args.pin,
        password=args.password,
        phone=args.phone,
        login_method=args.login_method,
        max_pages=args.max_pages,
        bo_url=args.bo_url,
        bo_username=args.bo_username,
        bo_password=args.bo_password,
        bo_totp=args.bo_totp,
    )

    print(f"\n{'='*60}")
    print(f"🤖 MP Crawler")
    print(f"   Target : {args.url}")
    print(f"   Method : {args.login_method}")
    print(f"   Max    : {args.max_pages} pages")
    print(f"   Output : {args.output}")
    print(f"{'='*60}")

    doc = asyncio.run(crawler.run())

    out_path = Path(args.output)
    out_path.write_text(doc, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"✅ Done! {args.output} written to: {out_path.resolve()}")
    print(f"   Pages crawled  : {len(crawler.visited)}")
    print(f"   Pages analysed : {len(crawler.page_notes)}")
    print(f"   Screenshots    : {SCREENSHOT_DIR}/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
