"""
app.py — Web UI for AI Tester Agent

Usage:
    python app.py
    # then open http://localhost:8080

For deployment:
    uvicorn app:app --host 0.0.0.0 --port 8080
"""

import asyncio
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent
OUTPUT_DIR  = BASE_DIR / "output"
UPLOADS_DIR = BASE_DIR / "uploads"
STATIC_DIR  = BASE_DIR / "static"
FEEDBACK_FILE         = BASE_DIR / "feedback_log.json"
FEEDBACK_CONTEXT_FILE = BASE_DIR / "feedback_context.md"
JOBS_STORE_FILE       = BASE_DIR / "jobs_store.json"

OUTPUT_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="AI Tester Agent")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory job store  {job_id -> {status, logs, output_file, test_cases, logic_gaps, ...}}
jobs: dict[str, dict] = {}


def _load_jobs_store() -> None:
    """Load persisted completed jobs from disk into the in-memory store on startup."""
    if not JOBS_STORE_FILE.exists():
        return
    try:
        stored = json.loads(JOBS_STORE_FILE.read_text(encoding="utf-8"))
        for job_id, job in stored.items():
            if job_id not in jobs:
                jobs[job_id] = {**job, "logs": []}  # logs not persisted (too large)
    except (json.JSONDecodeError, OSError):
        pass


def _persist_job(job_id: str) -> None:
    """Append or update a completed job in the on-disk store."""
    try:
        stored: dict = {}
        if JOBS_STORE_FILE.exists():
            try:
                stored = json.loads(JOBS_STORE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                stored = {}
        job = jobs[job_id]
        stored[job_id] = {
            k: v for k, v in job.items() if k != "logs"  # skip large log list
        }
        JOBS_STORE_FILE.write_text(json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # persistence is best-effort — never block the job from completing


def _parse_html_stats(html_path: Path) -> dict:
    """Count test cases / logic gaps / concerns directly from the generated HTML report."""
    try:
        content = html_path.read_text(encoding="utf-8")
        tc    = len(re.findall(r'<tr data-priority=', content))
        gaps  = len(re.findall(r'class="card gap-card"', content))
        conc  = len(re.findall(r'class="card concern-card"', content))
        return {"test_cases": tc or None, "logic_gaps": gaps or None, "concerns": conc or None}
    except Exception:
        return {}


_load_jobs_store()


# ── Feedback helpers ──────────────────────────────────────────────────────────

FEEDBACK_TYPES = {
    "missed_scenario": "Missed Scenario",
    "incorrect_gap":   "Incorrect Logic Gap",
    "product_logic":   "Product Logic",
    "general":         "General Feedback",
}


def _load_feedback() -> list[dict]:
    if FEEDBACK_FILE.exists():
        try:
            return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_feedback(entries: list[dict]) -> None:
    FEEDBACK_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def _compile_feedback_context(entries: list[dict]) -> None:
    """Regenerate feedback_context.md from all approved/active feedback entries."""
    if not entries:
        if FEEDBACK_CONTEXT_FILE.exists():
            FEEDBACK_CONTEXT_FILE.unlink()
        return

    by_type: dict[str, list[dict]] = {t: [] for t in FEEDBACK_TYPES}
    for e in entries:
        t = e.get("type", "general")
        by_type.setdefault(t, []).append(e)

    sections = []

    section_meta = [
        ("product_logic",   "## Product Logic Notes",
         "Business rules and domain knowledge that must always be considered:"),
        ("missed_scenario", "## Commonly Missed Scenarios",
         "Scenarios the AI previously missed — always check these when relevant:"),
        ("incorrect_gap",   "## Logic Gap Corrections",
         "Cases where a flagged logic gap was actually incorrect:"),
        ("general",         "## General Guidance",
         "General quality feedback and process notes:"),
    ]

    for ftype, heading, description in section_meta:
        items = by_type.get(ftype, [])
        if not items:
            continue
        lines = [heading, description]
        for item in items:
            date = item.get("created_at", "")[:10]
            env  = f"[{item['env'].upper()}] " if item.get("env") else ""
            ref  = f" (re: {item['filename']})" if item.get("filename") else ""
            lines.append(f"- [{date}] {env}{item['content'].strip()}{ref}")
        sections.append("\n".join(lines))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        "# Accumulated QA Feedback & Learnings\n\n"
        "> Auto-generated from team feedback. "
        "Use this as additional context when analysing requirements.\n"
        f"> Last updated: {now}\n"
    )

    FEEDBACK_CONTEXT_FILE.write_text(
        header + "\n\n" + "\n\n".join(sections) + "\n",
        encoding="utf-8",
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.post("/run")
async def start_job(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    env: str = Form(default=""),
):
    job_id = uuid.uuid4().hex[:8]

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (file.filename or "req.md"))
    tmp_path = UPLOADS_DIR / f"{job_id}_{safe_name}"
    tmp_path.write_bytes(await file.read())

    jobs[job_id] = {
        "status": "running",
        "logs": [],
        "output_file": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "filename": file.filename,
        "env": env or None,
        "test_cases": None,
        "logic_gaps": None,
        "concerns": None,
    }

    background_tasks.add_task(_run_pipeline, job_id, str(tmp_path), env or None)
    return {"job_id": job_id}


@app.get("/logs/{job_id}")
async def stream_logs(job_id: str):
    if job_id not in jobs:
        return HTMLResponse("Job not found", status_code=404)

    async def event_generator():
        sent = 0
        while True:
            job = jobs.get(job_id, {})
            logs = job.get("logs", [])

            while sent < len(logs):
                payload = json.dumps({"type": "log", "text": logs[sent]})
                yield f"data: {payload}\n\n"
                sent += 1

            if job.get("status") in ("done", "error"):
                payload = json.dumps({
                    "type": "done",
                    "status": job["status"],
                    "output_file": job.get("output_file"),
                    "test_cases": job.get("test_cases"),
                    "logic_gaps": job.get("logic_gaps"),
                })
                yield f"data: {payload}\n\n"
                break

            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/report/{job_id}", response_class=HTMLResponse)
async def get_report(job_id: str):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return HTMLResponse("<h1>Report not ready</h1>", status_code=404)
    output_file = job.get("output_file")
    if not output_file:
        return HTMLResponse("<h1>Output file path not found</h1>", status_code=404)
    path = BASE_DIR / output_file
    if not path.exists():
        return HTMLResponse(f"<h1>File not found: {output_file}</h1>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/download/{job_id}")
async def download_report(job_id: str):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return HTMLResponse("Not ready", status_code=404)
    path = BASE_DIR / job["output_file"]
    return FileResponse(path=str(path), filename=path.name, media_type="text/html")


@app.get("/jobs")
async def list_jobs():
    return [
        {
            "job_id": jid,
            "status": j["status"],
            "filename": j["filename"],
            "env": j["env"],
            "started_at": j["started_at"],
            "finished_at": j.get("finished_at"),
            "test_cases": j.get("test_cases"),
            "logic_gaps": j.get("logic_gaps"),
            "concerns": j.get("concerns"),
            "output_file": j.get("output_file"),
        }
        for jid, j in reversed(list(jobs.items()))
    ]


@app.get("/stats")
async def get_stats():
    all_jobs = list(jobs.values())
    done = [j for j in all_jobs if j["status"] == "done"]
    total_tests = sum(j["test_cases"] or 0 for j in done)
    total_gaps = sum(j["logic_gaps"] or 0 for j in done)
    env_counts = {}
    for j in all_jobs:
        e = j.get("env") or "all"
        env_counts[e] = env_counts.get(e, 0) + 1
    return {
        "total_runs": len(all_jobs),
        "done_runs": len(done),
        "error_runs": sum(1 for j in all_jobs if j["status"] == "error"),
        "running_runs": sum(1 for j in all_jobs if j["status"] == "running"),
        "total_test_cases": total_tests,
        "total_logic_gaps": total_gaps,
        "env_counts": env_counts,
    }


# ── Feedback routes ───────────────────────────────────────────────────────────

@app.get("/feedback")
async def get_feedback():
    return _load_feedback()


@app.post("/feedback")
async def add_feedback(
    content:  str = Form(...),
    type:     str = Form(default="general"),
    job_id:   str = Form(default=""),
    env:      str = Form(default=""),
    filename: str = Form(default=""),
):
    if not content.strip():
        return HTMLResponse("content is required", status_code=400)
    if type not in FEEDBACK_TYPES:
        return HTMLResponse("invalid type", status_code=400)

    entry = {
        "id":         uuid.uuid4().hex[:12],
        "created_at": datetime.now().isoformat(),
        "type":       type,
        "content":    content.strip(),
        "job_id":     job_id or None,
        "env":        env or None,
        "filename":   filename or None,
    }
    entries = _load_feedback()
    entries.append(entry)
    _save_feedback(entries)
    _compile_feedback_context(entries)
    return entry


@app.delete("/feedback/{feedback_id}")
async def delete_feedback(feedback_id: str):
    entries = [e for e in _load_feedback() if e["id"] != feedback_id]
    _save_feedback(entries)
    _compile_feedback_context(entries)
    return {"ok": True}


@app.get("/feedback/context")
async def get_feedback_context():
    if FEEDBACK_CONTEXT_FILE.exists():
        return {"content": FEEDBACK_CONTEXT_FILE.read_text(encoding="utf-8")}
    return {"content": ""}


# ── Pipeline runner ───────────────────────────────────────────────────────────

_RE_TEST_CASES = re.compile(r'(\d+)\s+test cases?', re.IGNORECASE)
_RE_LOGIC_GAPS = re.compile(r'(\d+)\s+logic gaps?', re.IGNORECASE)
_RE_CONCERNS   = re.compile(r'(\d+)\s+concerns?', re.IGNORECASE)


async def _run_pipeline(job_id: str, file_path: str, env: str | None):
    cmd = [sys.executable, str(BASE_DIR / "main.py"), "--input-file", file_path]
    if env:
        cmd.extend(["--env", env])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BASE_DIR),
        )

        output_file = None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            jobs[job_id]["logs"].append(line)

            if "HTML report:" in line:
                output_file = line.split("HTML report:")[-1].strip()

            if m := _RE_TEST_CASES.search(line):
                jobs[job_id]["test_cases"] = int(m.group(1))

            if m := _RE_LOGIC_GAPS.search(line):
                jobs[job_id]["logic_gaps"] = int(m.group(1))

            if m := _RE_CONCERNS.search(line):
                jobs[job_id]["concerns"] = int(m.group(1))

        await proc.wait()
        jobs[job_id]["finished_at"] = datetime.now().isoformat()

        if proc.returncode == 0:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["output_file"] = output_file

            # Use the HTML report as the authoritative stats source — more reliable
            # than regex-parsing stdout, and guaranteed to match what the user sees.
            if output_file:
                html_path = BASE_DIR / output_file
                stats = _parse_html_stats(html_path)
                jobs[job_id].update(stats)
        else:
            jobs[job_id]["status"] = "error"

        _persist_job(job_id)

    except Exception as exc:
        jobs[job_id]["logs"].append(f"❌ Server error: {exc}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["finished_at"] = datetime.now().isoformat()
        _persist_job(job_id)


# ── Dev server ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
