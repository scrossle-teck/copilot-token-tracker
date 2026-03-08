from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re
from sqlite3 import Connection, Row

from tokentracker.pricing import (
    describe_cost_strategy,
    estimate_model_row_currency_amount,
    estimate_total_currency_amount,
    model_pricing_for,
    premium_request_rate,
    pricing_currency,
)
from tokentracker.storage import (
    fetch_daily_breakdown,
    fetch_model_breakdown,
    fetch_recent_sessions,
    fetch_repo_breakdown,
    fetch_session_cost_rows,
    fetch_summary,
)


def project_dashboard_path(output_path: Path, scope: str) -> Path:
    return output_path.parent / "projects" / f"{_scope_slug(scope)}.html"


def render_dashboard(connection: Connection, output_path: Path, pricing: dict[str, object]) -> Path:
    repo_rows = fetch_repo_breakdown(connection)
    project_links = {
        str(row["scope"]): f"projects/{project_dashboard_path(output_path, str(row['scope'])).name}"
        for row in repo_rows
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_dashboard_html(
            connection,
            pricing=pricing,
            scope=None,
            project_links=project_links,
            overview_href=None,
        ),
        encoding="utf-8",
    )

    projects_dir = output_path.parent / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    for row in repo_rows:
        scope = str(row["scope"])
        project_path = project_dashboard_path(output_path, scope)
        project_path.write_text(
            _render_dashboard_html(
                connection,
                pricing=pricing,
                scope=scope,
                project_links=None,
                overview_href=f"../{output_path.name}",
            ),
            encoding="utf-8",
        )
    return output_path


def _render_dashboard_html(
    connection: Connection,
    pricing: dict[str, object],
    scope: str | None,
    project_links: dict[str, str] | None,
    overview_href: str | None,
) -> str:
    summary = fetch_summary(connection, scope=scope)
    model_rows = fetch_model_breakdown(connection, scope=scope)
    repo_rows = fetch_repo_breakdown(connection, scope=scope)
    daily_rows = fetch_daily_breakdown(connection, scope=scope)
    recent_rows = fetch_recent_sessions(connection, scope=scope)
    cost_rows = fetch_session_cost_rows(connection, scope=scope)

    currency = pricing_currency(pricing)
    cost_strategy = describe_cost_strategy(pricing)
    total_cost = estimate_total_currency_amount(model_rows, pricing)
    daily_costs = _group_estimated_costs(cost_rows, "day", pricing)
    repo_costs = _group_estimated_costs(cost_rows, "scope", pricing)
    recent_costs = _group_estimated_costs(cost_rows, "session_id", pricing)
    page_title = "Copilot CLI Token Tracker" if scope is None else f"Copilot CLI Token Tracker - {scope}"
    scope_notice = (
        ""
        if scope is None
        else f'<p class="meta">Project view: <code>{escape(scope)}</code></p>'
    )
    overview_link = (
        ""
        if overview_href is None
        else f'<p class="meta"><a href="{escape(overview_href)}">Back to overview</a></p>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(page_title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f172a;
      --panel: #111827;
      --card: #1f2937;
      --muted: #94a3b8;
      --text: #e5e7eb;
      --accent: #60a5fa;
      --border: #334155;
      --good: #22c55e;
    }}
    body {{
      margin: 0;
      font-family: Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.4;
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 32px 24px 64px;
    }}
    h1, h2 {{
      margin: 0 0 12px;
    }}
    p, li {{
      color: var(--muted);
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin: 24px 0 32px;
    }}
    .card, .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px 18px;
      box-shadow: 0 8px 30px rgba(0, 0, 0, 0.15);
      overflow: hidden;
    }}
    .card .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .card .value {{
      margin-top: 10px;
      font-size: 28px;
      font-weight: 700;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 20px;
      margin-bottom: 20px;
    }}
    .table-wrap {{
      width: 100%;
      overflow-x: auto;
      overflow-y: hidden;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
      min-width: 640px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .wrap-cell {{
      max-width: 0;
    }}
    .nowrap {{
      white-space: nowrap;
    }}
    .numeric {{
      text-align: right;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }}
    .bar-cell {{
      min-width: 180px;
    }}
    .bar {{
      height: 10px;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), #38bdf8);
      width: var(--bar-width);
      min-width: 2px;
    }}
    .meta {{
      margin-top: 6px;
      font-size: 12px;
      color: var(--muted);
    }}
    .notice {{
      border-left: 4px solid var(--accent);
      padding-left: 14px;
      margin-bottom: 24px;
    }}
    code {{
      color: #bfdbfe;
    }}
    a {{
      color: #bfdbfe;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{escape(page_title)}</h1>
      <p>Aggregated from completed <code>session.shutdown</code> events in <code>~/.copilot/session-state/*/events.jsonl</code>.</p>
      {scope_notice}
      {overview_link}
      <p class="meta">Generated {escape(_now_iso())}</p>
    </header>

    <section class="notice">
      <p>This dashboard tracks completed local sessions only. Active sessions are imported after they emit a <code>session.shutdown</code> event. Personal hooks sync at both session start and session end, so a missed shutdown is backfilled on the next run.</p>
      <p>Older Copilot CLI session folders may still exist before these totals begin. Versions before <code>0.0.422</code> did not persist the completed-session <code>session.shutdown</code> usage summary this tracker relies on, so those earlier sessions cannot be backfilled into token or cost totals.</p>
      <p>Estimated cost mode: <strong>{escape(cost_strategy)}</strong>. Edit <code>pricing.json</code> in this tracker data directory to set per-model token pricing and/or a premium-request conversion rate.</p>
    </section>

    <section class="cards">
      {_card("Completed sessions", _format_int(summary["session_count"]))}
      {_card("Total tokens", _format_int(summary["total_tokens"]))}
      {_card("Total requests", _format_int(summary["total_requests"]))}
      {_card("Premium request units", _format_decimal(summary["total_premium_requests"]))}
      {_card("Session duration", _format_duration(summary["duration_seconds"]))}
      {_card("Code changes", f"{_format_int(summary['lines_added'])} added / {_format_int(summary['lines_removed'])} removed")}
      {_card("Input tokens", _format_int(summary["total_input_tokens"]))}
      {_card("Output tokens", _format_int(summary["total_output_tokens"]))}
      {_card(f"Estimated cost ({currency})", _format_currency(total_cost, currency))}
    </section>

    <div class="grid">
      <section class="panel">
        <h2>Daily trend</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="nowrap">Day</th>
                <th class="numeric">Sessions</th>
                <th class="numeric">Tokens</th>
                <th class="bar-cell">Usage</th>
                <th class="numeric">Premium units</th>
                <th class="numeric">Estimated cost</th>
              </tr>
            </thead>
            <tbody>
              {_daily_rows(daily_rows, daily_costs, currency)}
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <h2>By model</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th class="numeric">Sessions</th>
                <th class="numeric">Requests</th>
                <th class="numeric">Tokens</th>
                <th class="numeric">Premium units</th>
                <th>Pricing basis</th>
                <th class="numeric">Estimated cost</th>
              </tr>
            </thead>
            <tbody>
              {_model_rows(model_rows, pricing, currency)}
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <div class="grid">
      <section class="panel">
        <h2>By repo or cwd</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Scope</th>
                <th class="numeric">Sessions</th>
                <th class="numeric">Requests</th>
                <th class="numeric">Tokens</th>
                <th class="numeric">Duration</th>
                <th class="numeric">Estimated cost</th>
              </tr>
            </thead>
            <tbody>
              {_repo_rows(repo_rows, repo_costs, currency, project_links)}
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <h2>Recent sessions</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="nowrap">Start</th>
                <th>Scope</th>
                <th>Model</th>
                <th class="numeric">Tokens</th>
                <th class="numeric">Premium units</th>
                <th class="numeric">Estimated cost</th>
              </tr>
            </thead>
            <tbody>
              {_recent_rows(recent_rows, recent_costs, currency)}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </main>
</body>
</html>
"""


def _card(label: str, value: str) -> str:
    return f'<article class="card"><div class="label">{escape(label)}</div><div class="value">{escape(value)}</div></article>'


def _daily_rows(rows: list[Row], cost_by_day: dict[str, float], currency: str) -> str:
    if not rows:
        return _empty_row(6, "No completed sessions imported yet.")
    max_tokens = max(int(row["total_tokens"]) for row in rows) or 1
    rendered: list[str] = []
    for row in rows:
        day = str(row["day"])
        width = max(2, round((int(row["total_tokens"]) / max_tokens) * 100))
        rendered.append(
            "<tr>"
            f"<td class=\"nowrap\">{escape(day)}</td>"
            f"<td class=\"numeric\">{_format_int(row['session_count'])}</td>"
            f"<td class=\"numeric\">{_format_int(row['total_tokens'])}</td>"
            f"<td class=\"bar-cell\"><div class=\"bar\" style=\"--bar-width:{width}%\"></div></td>"
            f"<td class=\"numeric\">{_format_decimal(row['total_premium_requests'])}</td>"
            f"<td class=\"numeric\">{_format_currency(cost_by_day.get(day), currency)}</td>"
            "</tr>"
        )
    return "".join(rendered)


def _model_rows(rows: list[Row], pricing: dict[str, object], currency: str) -> str:
    if not rows:
        return _empty_row(7, "No model metrics available yet.")
    rendered: list[str] = []
    for row in rows:
        estimated_cost = estimate_model_row_currency_amount(row, pricing)
        pricing_basis = _describe_model_pricing(str(row["model_name"]), pricing, currency)
        rendered.append(
            "<tr>"
            f"<td class=\"wrap-cell\">{escape(str(row['model_name']))}</td>"
            f"<td class=\"numeric\">{_format_int(row['session_count'])}</td>"
            f"<td class=\"numeric\">{_format_int(row['request_count'])}</td>"
            f"<td class=\"numeric\">{_format_int(row['total_tokens'])}</td>"
            f"<td class=\"numeric\">{_format_decimal(row['premium_request_cost'])}</td>"
            f"<td class=\"wrap-cell\">{escape(pricing_basis)}</td>"
            f"<td class=\"numeric\">{_format_currency(estimated_cost, currency)}</td>"
            "</tr>"
        )
    return "".join(rendered)


def _repo_rows(
    rows: list[Row],
    repo_costs: dict[str, float],
    currency: str,
    project_links: dict[str, str] | None = None,
) -> str:
    if not rows:
        return _empty_row(6, "No repository or cwd usage available yet.")
    rendered: list[str] = []
    for row in rows:
        scope = str(row["scope"])
        scope_text = escape(scope)
        href = None if project_links is None else project_links.get(scope)
        scope_cell = scope_text if href is None else f'<a href="{escape(href)}">{scope_text}</a>'
        rendered.append(
            "<tr>"
            f"<td class=\"wrap-cell\">{scope_cell}</td>"
            f"<td class=\"numeric\">{_format_int(row['session_count'])}</td>"
            f"<td class=\"numeric\">{_format_int(row['total_requests'])}</td>"
            f"<td class=\"numeric\">{_format_int(row['total_tokens'])}</td>"
            f"<td class=\"numeric\">{_format_duration(row['duration_seconds'])}</td>"
            f"<td class=\"numeric\">{_format_currency(repo_costs.get(scope), currency)}</td>"
            "</tr>"
        )
    return "".join(rendered)


def _recent_rows(rows: list[Row], recent_costs: dict[str, float], currency: str) -> str:
    if not rows:
        return _empty_row(6, "No recent completed sessions available yet.")
    return "".join(
        "<tr>"
        f"<td class=\"nowrap\">{escape(_trim_timestamp(row['started_at'] or row['shutdown_at']))}</td>"
        f"<td class=\"wrap-cell\">{escape(str(row['scope']))}</td>"
        f"<td class=\"wrap-cell\">{escape(str(row['current_model'] or '<unknown>'))}</td>"
        f"<td class=\"numeric\">{_format_int(row['total_tokens'])}</td>"
        f"<td class=\"numeric\">{_format_decimal(row['total_premium_requests'])}</td>"
        f"<td class=\"numeric\">{_format_currency(recent_costs.get(str(row['session_id'])), currency)}</td>"
        "</tr>"
        for row in rows
    )


def _group_estimated_costs(rows: list[Row], key_name: str, pricing: dict[str, object]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        amount = estimate_model_row_currency_amount(row, pricing)
        if amount is None:
            continue
        key = str(row[key_name])
        totals[key] = totals.get(key, 0.0) + amount
    return totals


def _empty_row(columns: int, text: str) -> str:
    return f'<tr><td colspan="{columns}">{escape(text)}</td></tr>'


def _describe_model_pricing(model_name: str, pricing: dict[str, object], currency: str) -> str:
    model_rates = model_pricing_for(model_name, pricing)
    if model_rates is not None:
        input_rate = model_rates.get("inputCostPer1M")
        output_rate = model_rates.get("outputCostPer1M")
        if input_rate is not None and output_rate is not None:
            return (
                f"Input {_format_rate(float(input_rate), currency)}; "
                f"Output {_format_rate(float(output_rate), currency)}; "
                f"Cache read {_format_rate(float(model_rates.get('cacheReadCostPer1M') or 0.0), currency)}; "
                f"Cache write {_format_rate(float(model_rates.get('cacheWriteCostPer1M') or 0.0), currency)} / 1M tokens"
            )
    rate = premium_request_rate(pricing)
    if rate is not None:
        return f"Premium fallback {_format_rate(rate, currency)} per unit"
    return "Not configured"


def _format_int(value: object) -> str:
    return f"{int(value):,}"


def _format_decimal(value: object) -> str:
    return f"{float(value):,.2f}"


def _format_rate(value: float, currency: str = "USD") -> str:
    text = f"{value:,.3f}".rstrip("0").rstrip(".")
    return f"${text}" if currency.upper() == "USD" else f"{currency.upper()} {text}"


def _format_duration(value: object) -> str:
    total_seconds = int(value or 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_currency(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "Not configured"
    text = f"{value:,.2f}"
    return f"${text}" if currency.upper() == "USD" else f"{currency.upper()} {text}"


def _trim_timestamp(value: str) -> str:
    return value.replace("T", " ").replace("Z", "")


def _scope_slug(scope: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", scope.strip()).strip("-").lower()
    if not base:
        base = "project"
    digest = hashlib.sha1(scope.encode("utf-8")).hexdigest()[:8]
    return f"{base[:48]}-{digest}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

