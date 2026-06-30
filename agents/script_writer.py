"""
agents/script_writer.py

Script Writer Agent — Generate Playwright TypeScript test scripts
based on test cases from the Test Designer Agent and patterns from the existing repo.

Output: list of .spec.ts file contents, grouped by feature/flow.
"""

import json
from pathlib import Path
from config.settings import OUTPUT_DIR
from tools.claude_code_client import ClaudeCodeClient

SYSTEM_PROMPT = """Bạn là một Senior QA Automation Engineer chuyên viết Playwright TypeScript tests.

Nhiệm vụ: Chuyển đổi test cases thành Playwright TypeScript scripts hoàn chỉnh.

**Nguyên tắc bắt buộc:**
1. Theo đúng pattern của repo hiện có (Page Object, fixtures, naming)
2. Sử dụng `test.describe` để nhóm test cases liên quan
3. Mỗi `test()` tương ứng với 1 test case ID
4. Comment `// TC-XXX: Tên test case` trước mỗi test block
5. Sử dụng `expect()` với assertion có ý nghĩa
6. Xử lý async/await đúng cách
7. Không hardcode URL — dùng env hoặc base URL từ config
8. Precondition → xử lý trong `test.beforeEach` hoặc đầu test

**Cấu trúc file output:**
```typescript
import { test, expect } from '@playwright/test';
// import page objects từ repo nếu có

test.describe('Feature Name', () => {
  test.beforeEach(async ({ page }) => {
    // setup chung nếu cần
  });

  // TC-001: Tên test case
  test('should [hành động] when [điều kiện]', async ({ page }) => {
    // Arrange
    // Act
    // Assert
  });
});
```

**Naming convention cho test:**
- `should [verb] [expected outcome]`
- `should display error when [invalid condition]`
- `should navigate to [destination] after [action]`

Trả lời PHẢI là JSON hợp lệ (không có text ngoài JSON):
{
  "files": [
    {
      "filename": "string — ví dụ: login.spec.ts",
      "description": "string — mô tả ngắn về file",
      "content": "string — toàn bộ TypeScript code"
    }
  ],
  "notes": [
    "string — ghi chú về assumptions, mock data cần chuẩn bị, v.v."
  ]
}"""


USER_PROMPT_TEMPLATE = """Hãy generate Playwright TypeScript scripts cho các test cases sau:

## THÔNG TIN FEATURE

**Feature:** {feature_name}

---

## TEST CASES CẦN IMPLEMENT

{test_cases_json}

---

## REPO PATTERN (học theo cấu trúc này)

{repo_context}

---

## YÊU CẦU THÊM

- Nhóm test cases theo flow hợp lý (auth flow, CRUD flow, validation flow, v.v.)
- Mỗi nhóm tạo 1 file .spec.ts riêng
- Nếu có Page Object trong repo, tạo thêm page object mới theo đúng pattern
- Với test cases cần data setup (user đã tồn tại, record đã tạo), dùng API setup trong beforeAll hoặc fixture
- Comment rõ ràng phần nào cần điều chỉnh theo môi trường thực tế"""


class ScriptWriterAgent:
    def __init__(self):
        self.client = ClaudeCodeClient()

    async def generate_scripts(
        self,
        test_suite: dict,
        repo_context: str,
        feature_name: str = "Feature",
    ) -> dict:
        """
        Generate Playwright scripts from test cases.

        Args:
            test_suite: dict from TestDesignerAgent.design_tests()
            repo_context: string from RepoReader.read_patterns()
            feature_name: feature name used for file naming

        Returns:
            dict with files (list) and notes (list)
        """
        test_cases = test_suite.get("test_cases", [])

        # Format test cases as compact JSON
        test_cases_json = json.dumps(test_cases, ensure_ascii=False, indent=2)

        # Limit repo context to avoid exceeding the context window
        max_repo_chars = 20_000
        if len(repo_context) > max_repo_chars:
            repo_context = repo_context[:max_repo_chars] + "\n\n[...repo context truncated...]"

        user_message = USER_PROMPT_TEMPLATE.format(
            feature_name=feature_name,
            test_cases_json=test_cases_json,
            repo_context=repo_context,
        )

        print("⚙️  Script Writer Agent is generating Playwright scripts...")

        result = self.client.call_json(SYSTEM_PROMPT, user_message, timeout=900)

        files = result.get("files", [])
        print(f"   ✓ {len(files)} file(s) generated:")
        for f in files:
            print(f"      - {f['filename']}: {f.get('description', '')}")

        return result

    def save_scripts(self, scripts: dict, output_subdir: str = "playwright") -> list[Path]:
        """
        Save scripts as .spec.ts files in the output directory.

        Returns:
            list of Paths that were created
        """
        output_path = OUTPUT_DIR / output_subdir
        output_path.mkdir(parents=True, exist_ok=True)

        saved_paths = []
        for file_info in scripts.get("files", []):
            filename = file_info.get("filename", "generated.spec.ts")
            content = file_info.get("content", "")

            # Ensure correct file extension
            if not filename.endswith(".ts"):
                filename = filename + ".ts"

            file_path = output_path / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            saved_paths.append(file_path)
            print(f"   💾 Saved: {file_path}")

        # Write notes if any
        notes = scripts.get("notes", [])
        if notes:
            notes_path = output_path / "NOTES.md"
            notes_content = "# Script Writer Notes\n\n" + "\n".join(f"- {n}" for n in notes)
            notes_path.write_text(notes_content, encoding="utf-8")
            saved_paths.append(notes_path)

        return saved_paths

    def get_scripts_as_text(self, scripts: dict) -> str:
        """Format scripts as text for display or writing to Notion."""
        parts = []
        for f in scripts.get("files", []):
            parts.append(f"### `{f['filename']}`\n{f.get('description', '')}")
            parts.append(f"```typescript\n{f.get('content', '')}\n```")

        notes = scripts.get("notes", [])
        if notes:
            parts.append("### Notes")
            parts.extend(f"- {n}" for n in notes)

        return "\n\n".join(parts)
