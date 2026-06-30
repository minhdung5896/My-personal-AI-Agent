"""
tools/html_reporter.py

Generate a self-contained HTML report from analysis + test_suite data.
"""

from datetime import datetime
from pathlib import Path


def generate_html(
    ticket_title: str,
    analysis: dict,
    test_suite: dict,
) -> str:
    summary = analysis.get("summary", {})
    concerns = analysis.get("concerns", [])
    gaps = analysis.get("logic_gaps", [])
    discrepancies = analysis.get("figma_discrepancies", [])
    test_cases = test_suite.get("test_cases", [])
    test_scope = test_suite.get("test_scope", [])
    cov = test_suite.get("coverage_summary", {})
    feature_name = summary.get("feature_name", ticket_title)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    # ── Test Cases HTML ──────────────────────────────────────────────────
    tc_rows = ""
    for tc in test_cases:
        priority = tc.get("priority", "Medium")
        p_class = {"High": "badge-high", "Medium": "badge-med", "Low": "badge-low"}.get(priority, "badge-med")
        t_class = f"type-{tc.get('type', 'functional')}"
        steps_html = "".join(f"<li>{_esc(s)}</li>" for s in tc.get("steps", []))
        notes_html = f'<p class="notes">📝 {_esc(tc["notes"])}</p>' if tc.get("notes") else ""
        tc_rows += f"""
        <tr data-priority="{priority}" data-type="{tc.get('type', 'functional')}">
          <td class="tc-id">{_esc(tc.get('id',''))}</td>
          <td>
            <div class="tc-title">{_esc(tc.get('title',''))}</div>
            <div class="tc-pre">Pre: {_esc(tc.get('precondition',''))}</div>
          </td>
          <td><span class="badge {p_class}">{priority}</span></td>
          <td><span class="badge {t_class}">{_esc(tc.get('type',''))}</span></td>
          <td><ol class="steps">{steps_html}</ol></td>
          <td class="expected">{_esc(tc.get('expected',''))}{notes_html}</td>
        </tr>"""

    # ── Concerns HTML ────────────────────────────────────────────────────
    concerns_html = ""
    for c in concerns:
        impact = c.get("impact", "Medium")
        icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(impact, "⚪")
        suggestion = f'<div class="suggestion">💡 {_esc(c["suggestion"])}</div>' if c.get("suggestion") else ""
        concerns_html += f"""
        <div class="card concern-card">
          <div class="card-header">
            <span class="concern-area">{_esc(c.get('area',''))}</span>
            <span class="badge badge-{'high' if impact=='High' else 'med' if impact=='Medium' else 'low'}">{icon} {impact}</span>
          </div>
          <p class="concern-q">{_esc(c.get('question',''))}</p>
          {suggestion}
        </div>"""

    # ── Logic Gaps HTML ──────────────────────────────────────────────────
    gaps_html = ""
    for g in gaps:
        sev = g.get("severity", "Minor")
        icon = {"Critical": "🚨", "Major": "⚠️", "Minor": "📝"}.get(sev, "📝")
        sev_class = {"Critical": "badge-high", "Major": "badge-med", "Minor": "badge-low"}.get(sev, "badge-low")
        gaps_html += f"""
        <div class="card gap-card">
          <div class="card-header">
            <span class="badge {sev_class}">{icon} {sev}</span>
          </div>
          <p class="gap-scenario"><strong>{_esc(g.get('scenario',''))}</strong></p>
          <p class="gap-missing">Missing: {_esc(g.get('missing',''))}</p>
        </div>"""

    # ── Coverage by type ─────────────────────────────────────────────────
    by_type = cov.get("by_type", {})
    type_badges = "".join(
        f'<span class="badge type-{t}">{t}: {n}</span>' for t, n in by_type.items()
    )

    # ── Acceptance criteria ──────────────────────────────────────────────
    ac_items = "".join(f"<li>{_esc(a)}</li>" for a in summary.get("acceptance_criteria", []))
    flow_items = "".join(f"<li>{_esc(f)}</li>" for f in summary.get("main_flows", []))
    scope_items = "".join(f"<li>{_esc(s)}</li>" for s in test_scope)
    actors_text = ", ".join(summary.get("actors", []))
    figma_html = ""
    if discrepancies:
        items = "".join(f"<li>{_esc(d.get('description',''))}</li>" for d in discrepancies)
        figma_html = f"""
        <section>
          <h2>🎨 Figma vs Requirement</h2>
          <ul class="disc-list">{items}</ul>
        </section>"""

    type_filter_btns = "".join(
        "<button class=\"filter-btn\" onclick=\"filter('type','" + _esc(t) + "')\">" + _esc(t) + " (" + str(n) + ")</button>"
        for t, n in by_type.items()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(feature_name)} — Test Suite</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          font-size: 14px; background: #f5f6fa; color: #1a1a2e; line-height: 1.5; }}
  a {{ color: #4361ee; }}

  /* Layout */
  .page {{ max-width: 1200px; margin: 0 auto; padding: 24px 20px 60px; }}

  /* Header */
  .report-header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                    color: #fff; border-radius: 12px; padding: 28px 32px; margin-bottom: 24px; }}
  .report-header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 6px; }}
  .report-header .meta {{ font-size: 12px; color: #94a3b8; }}
  .report-header .meta span {{ margin-right: 16px; }}

  /* Summary grid */
  .summary-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
  @media (max-width: 700px) {{ .summary-grid {{ grid-template-columns: 1fr; }} }}

  /* Stats bar */
  .stats-bar {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }}
  .stat-box {{ background: #fff; border-radius: 10px; padding: 16px 20px; flex: 1;
               min-width: 110px; box-shadow: 0 1px 4px rgba(0,0,0,.08); text-align: center; }}
  .stat-box .num {{ font-size: 32px; font-weight: 800; }}
  .stat-box .label {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: .5px; }}
  .stat-total .num {{ color: #4361ee; }}
  .stat-high  .num {{ color: #ef4444; }}
  .stat-med   .num {{ color: #f59e0b; }}
  .stat-low   .num {{ color: #22c55e; }}

  /* Section */
  section {{ background: #fff; border-radius: 10px; padding: 20px 24px;
             margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,.07); }}
  section h2 {{ font-size: 15px; font-weight: 700; margin-bottom: 14px;
                padding-bottom: 8px; border-bottom: 2px solid #f1f5f9; }}
  section ul {{ padding-left: 18px; }}
  section li {{ margin-bottom: 5px; color: #374151; }}

  /* Badges */
  .badge {{ display: inline-block; font-size: 11px; font-weight: 600;
            padding: 2px 8px; border-radius: 20px; white-space: nowrap; }}
  .badge-high {{ background: #fee2e2; color: #dc2626; }}
  .badge-med  {{ background: #fef3c7; color: #d97706; }}
  .badge-low  {{ background: #dcfce7; color: #16a34a; }}
  .type-functional   {{ background: #e0e7ff; color: #3730a3; }}
  .type-ui           {{ background: #fae8ff; color: #7e22ce; }}
  .type-validation   {{ background: #fff7ed; color: #c2410c; }}
  .type-permission   {{ background: #fef9c3; color: #854d0e; }}
  .type-integration  {{ background: #ecfeff; color: #0e7490; }}
  .type-performance  {{ background: #f0fdf4; color: #166534; }}

  /* Filters */
  .filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }}
  .filters label {{ font-size: 12px; color: #64748b; margin-right: 4px; }}
  .filter-btn {{ background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 20px;
                 padding: 4px 12px; font-size: 12px; cursor: pointer; font-family: inherit;
                 transition: background .15s; }}
  .filter-btn:hover, .filter-btn.active {{ background: #4361ee; color: #fff; border-color: #4361ee; }}

  /* Table */
  .tc-table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #f8fafc; color: #475569; font-size: 11px; font-weight: 700;
        text-transform: uppercase; letter-spacing: .4px; padding: 10px 12px;
        text-align: left; border-bottom: 2px solid #e2e8f0; white-space: nowrap; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #fafbff; }}
  tr.hidden {{ display: none; }}

  .tc-id {{ font-weight: 700; color: #4361ee; white-space: nowrap; font-size: 12px; }}
  .tc-title {{ font-weight: 600; color: #1e293b; margin-bottom: 3px; }}
  .tc-pre {{ font-size: 11px; color: #94a3b8; }}
  .steps {{ padding-left: 16px; color: #374151; }}
  .steps li {{ margin-bottom: 3px; }}
  .expected {{ color: #166534; font-weight: 500; }}
  .notes {{ font-size: 11px; color: #78716c; margin-top: 6px; background: #fef9c3;
            padding: 4px 8px; border-radius: 4px; }}

  /* Cards */
  .cards-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }}
  .card {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px; }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center;
                  margin-bottom: 8px; }}
  .concern-area {{ font-size: 11px; font-weight: 700; color: #475569;
                   text-transform: uppercase; letter-spacing: .4px; }}
  .concern-q {{ color: #1e293b; font-weight: 500; margin-bottom: 6px; }}
  .suggestion {{ font-size: 12px; color: #78716c; background: #f8fafc;
                 padding: 6px 10px; border-radius: 4px; margin-top: 6px; }}
  .gap-scenario {{ color: #1e293b; margin-bottom: 4px; }}
  .gap-missing {{ font-size: 12px; color: #64748b; }}

  .type-chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}

  /* Disc list */
  .disc-list {{ padding-left: 18px; }}
  .disc-list li {{ margin-bottom: 6px; color: #7e22ce; }}

  @media print {{
    body {{ background: #fff; }}
    .filters {{ display: none; }}
    tr.hidden {{ display: table-row !important; }}
    .page {{ padding: 0; }}
  }}
</style>
</head>
<body>
<div class="page">

  <!-- ── Header ─────────────────────────────────────────────────────── -->
  <div class="report-header">
    <h1>{_esc(feature_name)}</h1>
    <div class="meta">
      <span>📅 {generated_at}</span>
      <span>👤 {_esc(actors_text)}</span>
    </div>
    <p style="margin-top:10px;color:#cbd5e1;font-size:13px">{_esc(summary.get('objective',''))}</p>
  </div>

  <!-- ── Stats ──────────────────────────────────────────────────────── -->
  <div class="stats-bar">
    <div class="stat-box stat-total">
      <div class="num">{cov.get('total', 0)}</div>
      <div class="label">Test Cases</div>
    </div>
    <div class="stat-box stat-high">
      <div class="num">{cov.get('high', 0)}</div>
      <div class="label">High</div>
    </div>
    <div class="stat-box stat-med">
      <div class="num">{cov.get('medium', 0)}</div>
      <div class="label">Medium</div>
    </div>
    <div class="stat-box stat-low">
      <div class="num">{cov.get('low', 0)}</div>
      <div class="label">Low</div>
    </div>
    <div class="stat-box" style="flex:2;text-align:left">
      <div class="label" style="margin-bottom:6px">By Type</div>
      <div class="type-chips">{type_badges}</div>
    </div>
  </div>

  <!-- ── Summary ────────────────────────────────────────────────────── -->
  <div class="summary-grid">
    <section>
      <h2>🎯 Main Flows</h2>
      <ol style="padding-left:18px">{flow_items}</ol>
    </section>
    <section>
      <h2>✅ Acceptance Criteria</h2>
      <ul>{ac_items}</ul>
    </section>
  </div>

  {"<section><h2>🔬 Test Scope</h2><ul>" + scope_items + "</ul></section>" if test_scope else ""}

  <!-- ── Test Cases ─────────────────────────────────────────────────── -->
  <section>
    <h2>🧪 Test Cases ({cov.get('total', 0)})</h2>
    <div class="filters">
      <label>Priority:</label>
      <button class="filter-btn active" onclick="filter('priority','all')">All</button>
      <button class="filter-btn" onclick="filter('priority','High')">🔴 High ({cov.get('high',0)})</button>
      <button class="filter-btn" onclick="filter('priority','Medium')">🟡 Medium ({cov.get('medium',0)})</button>
      <button class="filter-btn" onclick="filter('priority','Low')">🟢 Low ({cov.get('low',0)})</button>
      {type_filter_btns}
    </div>
    <div class="tc-table-wrap">
      <table id="tc-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Title / Precondition</th>
            <th>Priority</th>
            <th>Type</th>
            <th>Steps</th>
            <th>Expected Result</th>
          </tr>
        </thead>
        <tbody>{tc_rows}</tbody>
      </table>
    </div>
  </section>

  <!-- ── Concerns ───────────────────────────────────────────────────── -->
  {"<section><h2>❓ Concerns (" + str(len(concerns)) + ")</h2><div class='cards-grid'>" + concerns_html + "</div></section>" if concerns else ""}

  <!-- ── Logic Gaps ─────────────────────────────────────────────────── -->
  {"<section><h2>⚠️ Logic Gaps (" + str(len(gaps)) + ")</h2><div class='cards-grid'>" + gaps_html + "</div></section>" if gaps else ""}

  {figma_html}

</div>

<script>
  let activeFilters = {{ priority: 'all', type: 'all' }};

  function filter(dimension, value) {{
    activeFilters[dimension] = value;
    document.querySelectorAll('.filter-btn').forEach(btn => {{
      if (btn.getAttribute('onclick') === `filter('${{dimension}}','${{value}}')`) {{
        btn.classList.add('active');
      }} else if (btn.getAttribute('onclick')?.startsWith(`filter('${{dimension}}'`)) {{
        btn.classList.remove('active');
      }}
    }});
    applyFilters();
  }}

  function applyFilters() {{
    document.querySelectorAll('#tc-table tbody tr').forEach(row => {{
      const p = row.dataset.priority;
      const t = row.dataset.type;
      const matchP = activeFilters.priority === 'all' || p === activeFilters.priority;
      const matchT = activeFilters.type   === 'all' || t === activeFilters.type;
      row.classList.toggle('hidden', !(matchP && matchT));
    }});
  }}
</script>
</body>
</html>"""


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def save_html_report(
    ticket_title: str,
    analysis: dict,
    test_suite: dict,
    output_path: Path,
) -> Path:
    html = generate_html(ticket_title, analysis, test_suite)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
