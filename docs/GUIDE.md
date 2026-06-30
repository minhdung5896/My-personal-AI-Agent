# AI Manual Tester Agent — Hướng dẫn toàn tập

## Mục lục
1. [Lấy API Keys](#1-lấy-api-keys)
2. [Cài đặt môi trường](#2-cài-đặt-môi-trường)
3. [Cấu trúc project](#3-cấu-trúc-project)
4. [Analyst Agent](#4-analyst-agent)
5. [Test Designer Agent](#5-test-designer-agent)
6. [Script Writer Agent](#6-script-writer-agent)
7. [Orchestrator](#7-orchestrator)
8. [Chạy toàn hệ thống](#8-chạy-toàn-hệ-thống)

---

## 1. Lấy API Keys

### 1.1 Anthropic API Key
1. Truy cập https://console.anthropic.com
2. Đăng ký / đăng nhập tài khoản
3. Vào **API Keys** → **Create Key**
4. Copy key, lưu vào `.env`: `ANTHROPIC_API_KEY=sk-ant-...`

### 1.2 Notion API Token
1. Truy cập https://www.notion.so/my-integrations
2. Click **New integration**
3. Đặt tên: `AI Tester Agent`, chọn workspace
4. Permissions cần bật:
   - ✅ Read content
   - ✅ Update content
   - ✅ Insert content
5. Copy **Internal Integration Token** → `NOTION_TOKEN=secret_...`
6. **Quan trọng**: Vào từng Notion page/database → Share → Invite integration vừa tạo

### 1.3 Figma API Token
1. Truy cập https://www.figma.com/settings → **Security**
2. Scroll xuống **Personal access tokens** → **Generate new token**
3. Đặt tên, chọn scope: `File content: Read-only`
4. Copy token → `FIGMA_TOKEN=figd_...`
5. Lấy **File Key** từ URL Figma: `figma.com/design/FILE_KEY/...`

---

## 2. Cài đặt môi trường

```bash
# Clone/tạo project
cd ai-tester-agent

# Tạo virtual environment Python
python -m venv venv
source venv/bin/activate  # Mac/Linux
# hoặc: venv\Scripts\activate  # Windows

# Cài dependencies
pip install anthropic httpx python-dotenv rich pydantic

# Tạo file .env
cp .env.example .env
# Điền các API keys vào .env
```

---

## 3. Cấu trúc project

```
ai-tester-agent/
├── .env                    # API keys (không commit)
├── .env.example            # Template
├── config/
│   └── settings.py         # Cấu hình tập trung
├── agents/
│   ├── orchestrator.py     # Điều phối 3 agent
│   ├── analyst.py          # Agent phân tích requirement
│   ├── test_designer.py    # Agent viết test case
│   └── script_writer.py    # Agent generate Playwright
├── mcp-servers/
│   ├── notion_client.py    # Đọc/ghi Notion
│   └── figma_client.py     # Đọc Figma design
├── prompts/
│   ├── analyst_prompt.py
│   ├── test_designer_prompt.py
│   └── script_writer_prompt.py
├── tools/
│   ├── repo_reader.py      # Đọc Playwright repo
│   └── formatter.py        # Format output
├── output/                 # Output tạm thời
└── main.py                 # Entry point
```
