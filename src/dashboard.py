"""Read-only dashboard for reviewing bot interactions.

Usage:
    ./venv/bin/python -m src.dashboard              # today
    ./venv/bin/python -m src.dashboard 2026-03-05   # specific date
    uvicorn src.dashboard:app --port 8081
"""

import sys
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from src.store import Store

def _get_store() -> Store:
    return Store()


async def dashboard(request: Request) -> HTMLResponse:
    date = request.query_params.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return HTMLResponse(TEMPLATE)


async def api_data(request: Request) -> JSONResponse:
    date = request.query_params.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store = _get_store()
    stats = store.get_stats(date)
    tokens = store.get_token_totals(date)
    interactions = store.get_interactions(date)
    return JSONResponse({
        "date": date,
        "stats": stats,
        "tokens": tokens,
        "interactions": interactions,
    })


app = Starlette(
    routes=[
        Route("/", dashboard),
        Route("/api/data", api_data),
    ],
)

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canon Bot — Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f5; color: #1a1a1a; max-width: 820px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 1.3rem; margin-bottom: 4px; }
  .date-nav { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; }
  .date-nav input { font: inherit; padding: 4px 8px; border: 1px solid #ccc; border-radius: 6px; }
  .date-nav button { padding: 4px 12px; background: #0066cc; color: #fff; border: none;
                     border-radius: 6px; font: inherit; cursor: pointer; }

  /* Stats bar */
  .stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 12px 18px;
          min-width: 120px; }
  .stat-val { font-size: 1.6rem; font-weight: 700; }
  .stat-label { font-size: .78rem; color: #666; margin-top: 2px; }

  /* Interaction cards */
  .interaction { background: #fff; border: 1px solid #ddd; border-radius: 10px;
                 padding: 16px 20px; margin-bottom: 12px; }
  .interaction.engage { border-left: 4px solid #28a745; }
  .interaction.skip { border-left: 4px solid #ccc; }
  .interaction.post { border-left: 4px solid #0066cc; }
  .interaction.rate-limited { border-left: 4px solid #ffc107; }

  .meta-row { font-size: .78rem; color: #888; margin-bottom: 6px; }
  .stimulus { font-size: .9rem; margin-bottom: 8px; border-left: 3px solid #ddd;
              padding-left: 10px; color: #444; }

  .badge { display: inline-block; padding: 2px 10px; border-radius: 12px;
           font-size: .75rem; font-weight: 600; text-transform: uppercase; }
  .badge.engage { background: #d4edda; color: #155724; }
  .badge.skip { background: #f0f0f0; color: #666; }
  .badge.post { background: #cce5ff; color: #004085; }
  .badge.rate-limited { background: #fff3cd; color: #856404; }

  .triage-reason { font-size: .85rem; margin-left: 6px; }
  .the-problem { font-size: .83rem; font-style: italic; color: #555; margin-top: 4px; }

  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .chip { background: #e8e8e8; padding: 2px 8px; border-radius: 10px; font-size: .75rem; }

  .comp-section { margin-top: 10px; }
  .mode-badge { font-size: .72rem; background: #e0e7ff; color: #3730a3;
                padding: 2px 8px; border-radius: 10px; font-weight: 600; }
  .post-card { background: #fafafa; border: 1px solid #eee; border-radius: 8px;
               padding: 12px 14px; margin-top: 8px; font-size: .9rem;
               line-height: 1.45; white-space: pre-wrap; }
  .char-count { font-size: .72rem; color: #888; margin-top: 2px; }
  .char-count.over { color: #dc3545; font-weight: 600; }
  .passage-ref { font-size: .78rem; color: #666; margin-top: 6px; }
  .skip-reason { font-size: .83rem; color: #888; font-style: italic; margin-top: 4px; }
  .tokens { font-size: .75rem; color: #999; margin-top: 4px; }
  .dry-tag { font-size: .7rem; background: #f0f0f0; padding: 1px 6px; border-radius: 8px;
             color: #888; margin-left: 6px; }

  .empty { text-align: center; color: #999; padding: 40px; }
  .loading { text-align: center; color: #666; padding: 40px; }
</style>
</head>
<body>
<h1>Canon Bot — Dashboard</h1>
<div class="date-nav">
  <input type="date" id="datePicker">
  <button onclick="loadDate()">Go</button>
  <button onclick="loadToday()">Today</button>
</div>

<div class="stats" id="stats"></div>
<div id="interactions"><div class="loading">Loading...</div></div>

<script>
const datePicker = document.getElementById('datePicker');

function esc(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function loadToday() {
  datePicker.value = new Date().toISOString().slice(0, 10);
  loadDate();
}

function loadDate() {
  const date = datePicker.value;
  if (!date) return;
  fetch('/api/data?date=' + date)
    .then(r => r.json())
    .then(render)
    .catch(e => {
      document.getElementById('interactions').innerHTML =
        '<div class="empty">Error loading data: ' + esc(e.message) + '</div>';
    });
}

function render(data) {
  // Stats
  const s = data.stats;
  const t = data.tokens;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="stat-val">${s.total_triaged}</div><div class="stat-label">Triaged</div></div>
    <div class="stat"><div class="stat-val">${s.total_engaged}</div><div class="stat-label">Engaged</div></div>
    <div class="stat"><div class="stat-val">${s.total_posted}</div><div class="stat-label">Posted</div></div>
    <div class="stat"><div class="stat-val">${(t.tokens_in + t.tokens_out).toLocaleString()}</div><div class="stat-label">Tokens</div></div>
  `;

  // Interactions
  const el = document.getElementById('interactions');
  const items = data.interactions;
  if (!items.length) {
    el.innerHTML = '<div class="empty">No interactions for ' + esc(data.date) + '</div>';
    return;
  }

  el.innerHTML = items.map(renderInteraction).join('');
}

function renderInteraction(ix) {
  let cls = 'skip';
  if (ix.composition_decision === 'post') cls = 'post';
  else if (ix.triage_engage) cls = 'engage';

  const ts = ix.timestamp ? ix.timestamp.slice(11, 19) : '';
  const dry = ix.dry_run ? '<span class="dry-tag">dry run</span>' : '';

  let html = `<div class="interaction ${cls}">`;
  html += `<div class="meta-row">${esc(ts)} UTC &middot; ${esc(ix.source)} ${dry}</div>`;
  html += `<div class="stimulus">${esc(ix.stimulus_text)}</div>`;

  // Triage
  const engBadge = ix.triage_engage
    ? '<span class="badge engage">Engage</span>'
    : '<span class="badge skip">Skip</span>';
  html += engBadge;
  html += `<span class="triage-reason">${esc(ix.triage_reason)}</span>`;

  if (ix.the_problem) {
    html += `<div class="the-problem">${esc(ix.the_problem)}</div>`;
  }

  if (ix.triage_queries && ix.triage_queries.length) {
    html += `<div class="chips">${ix.triage_queries.map(q => `<span class="chip">${esc(q)}</span>`).join('')}</div>`;
  }

  // Composition
  if (ix.triage_engage && ix.composition_decision) {
    html += '<div class="comp-section">';
    if (ix.composition_decision === 'post') {
      html += `<span class="mode-badge">${esc(ix.composition_mode)}</span>`;
      const posts = ix.posts || [];
      for (const text of posts) {
        const len = text.length;
        html += `<div class="post-card">${esc(text)}</div>`;
        html += `<div class="char-count ${len > 300 ? 'over' : ''}">${len}/300</div>`;
      }
      if (ix.passage_used) {
        const pu = ix.passage_used;
        html += `<div class="passage-ref">${esc(pu.poet)} &mdash; &ldquo;${esc(pu.poem_title)}&rdquo;</div>`;
      }
    } else {
      html += `<div class="skip-reason">${esc(ix.skip_reason || '(no reason)')}</div>`;
    }
    if (ix.tokens_in || ix.tokens_out) {
      html += `<div class="tokens">${ix.tokens_in} in / ${ix.tokens_out} out</div>`;
    }
    html += '</div>';
  }

  html += '</div>';
  return html;
}

// Init
datePicker.value = new Date().toISOString().slice(0, 10);
loadDate();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    port = 8081
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    print(f"  Dashboard at http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
