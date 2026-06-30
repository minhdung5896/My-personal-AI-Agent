# AI Manual Tester Agent

An AI agent that automates the Manual Tester workflow:
reads requirements from a local file or Notion + Figma design → analyses logic gaps → generates test cases → produces an HTML test report.

Two ways to authenticate with Claude:
- **API key** (recommended for server/Jenkins): set `ANTHROPIC_API_KEY` in `.env` — uses the Anthropic Python SDK, no CLI needed.
- **Claude Code CLI** (local dev): leave `ANTHROPIC_API_KEY` unset — falls back to `claude -p` subprocess.

---

## Requirements

- **Python 3.10+**
- **Anthropic API key** (`ANTHROPIC_API_KEY`) — for server / Jenkins deploy  
  *OR* **Claude Code** installed and logged in — for local dev only
- Figma Personal Access Token *(optional — only needed when requirements include Figma links)*
- Notion Integration Token *(optional — only needed for `--page-id` or `--batch` mode)*

---

## Project Structure

```
ai-tester-agent/
├── app.py                          # Web UI server (FastAPI) — start with: python app.py
├── main.py                         # CLI entry point
├── requirements.txt
├── .env.example                    # Config template
├── requirement_example.md          # Sample requirement file
│
│── product_context.md              # TTH1 Back Office context (auto-generated)
│── mp_context.md                   # TTH1 Member Portal context (auto-generated)
│── tph1_product_context.md         # TPH1 Back Office context (auto-generated)
│── tph1_context.md                 # TPH1 Member Portal context (auto-generated)
│
├── static/
│   └── index.html                  # Web UI single-page frontend
│
├── agents/
│   ├── orchestrator.py             # Pipeline coordinator — loads context, calls agents
│   ├── analyst.py                  # Analyses requirement + Figma, finds logic gaps
│   ├── test_designer.py            # Designs test scope + test cases
│   └── script_writer.py            # (paused) Playwright TypeScript generation
│
├── tools/
│   ├── bo_crawler.py               # Crawls a Back Office admin panel → product context
│   ├── mp_crawler.py               # Crawls a Member Portal (player site) → MP context
│   ├── claude_code_client.py       # Wrapper around Claude Code CLI subprocess
│   ├── github_reader.py            # Reads source code from GitHub for context
│   ├── html_reporter.py            # Generates the HTML test report
│   └── repo_reader.py              # Reads Playwright repo patterns
│
├── mcp_servers/
│   ├── file_client.py              # Reads requirements from local .md / .txt files
│   ├── notion_client.py            # (optional) Reads/writes Notion pages
│   └── figma_client.py             # Reads Figma design files
│
├── config/
│   └── settings.py                 # Centralised config + env validation
│
├── tests/
│   └── test_agents.py              # Unit tests (mock data, no API keys needed)
│
├── uploads/                        # Temporary requirement files uploaded via UI
└── output/                         # Generated HTML reports and screenshots
    ├── bo_screenshots/             # Screenshots taken during BO crawl
    └── mp_screenshots/             # Screenshots taken during MP crawl
```

---

## Installation

### Step 1 — Install Claude Code *(local dev only — skip for server deploy)*

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

Log in with your Claude Pro account. Skip this step entirely if you use an `ANTHROPIC_API_KEY`.

### Step 2 — Clone and install dependencies

```bash
git clone <your-repo>
cd ai-tester-agent

python3 -m venv .venv
source .venv/bin/activate      # Mac / Linux
# .venv\Scripts\activate       # Windows

pip install -r requirements.txt
playwright install chromium
```

### Step 3 — Configure .env

```bash
cp .env.example .env
```

For server / Jenkins deploy (recommended):

```env
# API key from https://console.anthropic.com → Settings → API Keys
ANTHROPIC_API_KEY=sk-ant-api03-...

# Figma — only needed when requirement files contain Figma links
FIGMA_TOKEN=figd_...
```

For local dev (Claude Code CLI already logged in, no API key needed):

```env
# Leave ANTHROPIC_API_KEY commented out — falls back to `claude -p` CLI

# Figma — only needed when requirement files contain Figma links
FIGMA_TOKEN=figd_...
```

**Get a Figma Token** *(if needed)*:
1. Go to https://www.figma.com/settings → **Security** → **Personal access tokens**
2. Generate a new token with scope `File content: Read-only`

---

## Web UI

The easiest way to use the agent — no CLI needed. Anyone on the team can upload a requirement file and get a report through a browser.

### Start the server

```bash
python app.py
# → open http://localhost:8080
```

### How it works

| Step | Action |
|---|---|
| **1. Upload** | Drop or browse for your `.md` / `.txt` requirement file |
| **2. Configure** | Select environment: TTH1, TPH1, or All |
| **3. Run** | Click **Run Analysis** — live logs stream in real time |
| **4. Report** | HTML report appears inline; **Open in new tab** or **Download** |

A **Recent Jobs** history panel tracks all runs in the current session.

### Deploy to a server

For team-wide access, deploy with uvicorn:

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

The server runs each job as an isolated subprocess — multiple team members can submit jobs concurrently without interference.

---

## Context Generation (BO + MP Crawlers)

The agent becomes much more accurate when it understands the product. The crawlers automate this by crawling the Back Office and Member Portal to generate structured markdown knowledge bases.

### Back Office (BO) Crawler

Crawls a BO admin panel (login + TOTP 2FA supported) and generates a `product_context.md`:

```bash
python tools/bo_crawler.py \
  --url https://admin.tth1.trykz.dev/ \
  --username "Andrey58+1" \
  --password "yourpassword" \
  --totp-secret YOUR_BASE32_TOTP_SECRET \
  --output product_context.md \
  --max-pages 40
```

| Argument | Description |
|---|---|
| `--url` | BO base URL |
| `--username` | Admin login username |
| `--password` | Admin password |
| `--totp-secret` | Base32 TOTP secret for 2FA (leave blank if no 2FA) |
| `--output` | Output file (default: `product_context.md`) |
| `--max-pages` | Max pages to crawl (default: 40) |

### Member Portal (MP) Crawler

Crawls a player-facing gaming portal and generates an MP context file.
Supports **three login methods** — detected automatically from the credentials you provide.

#### Method 1: Phone + PIN (e.g. TTH1)
```bash
python tools/mp_crawler.py \
  --url https://www.tth1.trykz.dev/ \
  --username 0981029121 \
  --pin 1111 \
  --output mp_context.md
```

#### Method 2: Username + Password (e.g. TPH1)
```bash
python tools/mp_crawler.py \
  --url https://www.tph1.trykz.dev/ \
  --username andrey9845758 \
  --password Andrey58 \
  --output tph1_context.md
```

#### Method 3: Phone + OTP (OTP fetched live from BO All OTPs page)
```bash
python tools/mp_crawler.py \
  --url https://www.tph1.trykz.dev/ \
  --phone 09182938112 \
  --login-method otp \
  --bo-url https://admin.tth1.trykz.dev/ \
  --bo-username "Andrey58+1" \
  --bo-password "yourpassword" \
  --bo-totp YOUR_BASE32_TOTP_SECRET \
  --output tph1_context.md
```

| Argument | Description |
|---|---|
| `--url` | MP base URL |
| `--login-method` | `auto` (default), `pin`, `password`, or `otp` |
| `--username` | Username or mobile number |
| `--pin` | PIN code (for `pin` method) |
| `--password` | Password (for `password` method) |
| `--phone` | Phone number (for `otp` method; falls back to `--username`) |
| `--bo-url` | BO admin URL to fetch OTP from (for `otp` method) |
| `--bo-username` | BO username (for `otp` method) |
| `--bo-password` | BO password (for `otp` method) |
| `--bo-totp` | BO TOTP secret (for `otp` method) |
| `--output` | Output file (default: `mp_context.md`) |
| `--max-pages` | Max pages to crawl (default: 40) |

### Multi-Environment Setup

If you run multiple environments (e.g. TTH1 and TPH1), crawl each with a distinct output filename:

| File | Source | Environment |
|---|---|---|
| `product_context.md` | BO crawler | TTH1 |
| `mp_context.md` | MP crawler | TTH1 |
| `tph1_product_context.md` | BO crawler | TPH1 |
| `tph1_context.md` | MP crawler | TPH1 |

The orchestrator discovers these files automatically via naming convention:
- Files matching `*_product_context.md` → loaded as BO context
- Files matching `*_context.md` (others) → loaded as MP context

---

## Running the Agent (CLI)

> **Prefer the browser?** Use `python app.py` instead — see [Web UI](#web-ui) above.

### Primary mode — Local requirement file

Write your requirement as a `.md` or `.txt` file and run:

```bash
python main.py --input-file Requirement.md
```

Add `--env` to load only the context pair for a specific environment:

```bash
# Test against TTH1 — loads product_context.md + mp_context.md
python main.py --input-file Requirement.md --env tth1

# Test against TPH1 — loads tph1_product_context.md + tph1_context.md
python main.py --input-file Requirement.md --env tph1
```

Omit `--env` to load all available context files simultaneously.

**Requirement file format:**

```markdown
# Feature Name

## Description
Requirement content, acceptance criteria...

## Edge Cases
Special scenarios to test...

Figma: https://www.figma.com/file/...   ← auto-detected if present
```

### Optional — Notion mode *(requires NOTION_TOKEN)*

Add to `.env`:
```env
NOTION_TOKEN=secret_...
```

```bash
# Process one ticket
python main.py --page-id abc123def456 --env tth1

# Dry run (don't write results back to Notion)
python main.py --page-id abc123def456 --dry-run --env tth1

# Batch: process all tickets with a given Status
python main.py --batch --status "Ready for QA" --max-tickets 10 --env tth1
```

**Get a Notion Token**:
1. Go to https://www.notion.so/my-integrations → **New integration**
2. Name: `AI Tester Agent`, enable **Read + Update + Insert content**
3. Copy the **Internal Integration Token**
4. Share your Notion page with the integration: **Share** → find the integration name → **Invite**

---

## Output

Results are saved to `output/`:

| File | Content |
|---|---|
| `output/<feature-name>.html` | Full HTML report — analysis, concerns, logic gaps, test cases |
| `output/bo_screenshots/` | Debug screenshots from BO crawl |
| `output/mp_screenshots/` | Debug screenshots from MP crawl |

The HTML report includes:
- **Feature summary** and acceptance criteria derived from the requirement
- **Concern questions** — ambiguities and clarification needs (High / Medium / Low)
- **Logic gaps** — unhandled edge cases and missing flows (Critical / Major / Minor)
- **Figma discrepancies** — conflicts between design and written requirement
- **Test suite** — test cases organised by scope with steps and expected outcomes

---

## All CLI Arguments (`main.py`)

| Argument | Description |
|---|---|
| `--input-file FILE` | Path to local requirement file (.md / .txt) |
| `--env tth1\|tph1` | Environment to scope context to; omit to load all |
| `--context FILE...` | Additional context files (related tickets, API docs) |
| `--page-id ID` | Notion page ID to process |
| `--dry-run` | Analyse without writing results back to Notion |
| `--batch` | Process all tickets matching `--status` from the database |
| `--database-id ID` | Notion Database ID (default: from `.env`) |
| `--status STATUS` | Ticket status filter for batch mode (default: `Ready for QA`) |
| `--max-tickets N` | Max tickets to process in batch mode (default: 5) |

---

## Adding a New Environment

1. Crawl its BO: `python tools/bo_crawler.py ... --output <name>_product_context.md`
2. Crawl its MP: `python tools/mp_crawler.py ... --output <name>_context.md`
3. Add an entry to `ENV_CONTEXT_MAP` in [agents/orchestrator.py](agents/orchestrator.py):
   ```python
   ENV_CONTEXT_MAP = {
       "tth1": {"bo": "product_context.md",         "mp": "mp_context.md",    ...},
       "tph1": {"bo": "tph1_product_context.md",    "mp": "tph1_context.md",  ...},
       "new":  {"bo": "new_product_context.md",     "mp": "new_context.md",
                "bo_label": "BACK OFFICE (NEW) CONTEXT",
                "mp_label": "MEMBER PORTAL (NEW) CONTEXT"},
   }
   ```
4. Run: `python main.py --input-file Requirement.md --env new`

---

## Customising Agent Prompts

| Agent | File | Variable |
|---|---|---|
| Analyst | [agents/analyst.py](agents/analyst.py) | `SYSTEM_PROMPT`, `USER_PROMPT_TEMPLATE` |
| Test Designer | [agents/test_designer.py](agents/test_designer.py) | `SYSTEM_PROMPT` |
| BO Crawler | [tools/bo_crawler.py](tools/bo_crawler.py) | `ANALYSIS_PROMPT`, `COMPILE_PROMPT` |
| MP Crawler | [tools/mp_crawler.py](tools/mp_crawler.py) | `ANALYSIS_PROMPT`, `COMPILE_PROMPT` |

---

## Unit Tests

```bash
python tests/test_agents.py
```

No API keys needed — tests use mock data.

---

## Roadmap

- [x] Analyst agent — requirement analysis + logic gap detection
- [x] Notion integration — read tickets, write concerns + test cases
- [x] Test Designer agent — structured test suite generation
- [x] Script Writer agent *(paused — Playwright TypeScript generation)*
- [x] Local file mode — run without Notion
- [x] HTML report output
- [x] Figma integration — auto-fetch design context from Figma URLs
- [x] GitHub integration — read source code for richer analysis
- [x] BO Crawler — auto-generate product context from Back Office admin panel
- [x] MP Crawler — auto-generate player context from Member Portal (pin / password / OTP login)
- [x] Multi-environment support — `--env tth1 | tph1`, paired BO+MP context loading
- [x] Web UI — browser-based interface with live log streaming and inline report viewer
- [ ] Slack notification on completion
- [ ] GitHub integration — auto-create branch + commit scripts
- [ ] Execute Playwright tests and report results
