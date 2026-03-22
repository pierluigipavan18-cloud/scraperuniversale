#!/usr/bin/env python3
"""ScraperUniversale - Web Dashboard.

Matrix-inspired dark UI for launching scrapes, viewing results, and downloading exports.
Run: python dashboard.py
Then open: http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from contextlib import asynccontextmanager

from scraper.engine import ScraperEngine
from scraper.europages import EuropagesScraper
from scraper.kompass import KompassScraper
from scraper.wlw import WLWScraper
from scraper.industrystock import IndustryStockScraper
from scraper.paginegialle import PagineGialleScraper
from scraper.csea_energivori import CSEAEnergivoriScraper
from scraper.email_enricher import EmailEnricher
from scraper.models import Company
from export.dedup import clean_companies
from export.exporter import export

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO)

# --- State ---

jobs: dict[str, dict[str, Any]] = {}
websockets: dict[str, list[WebSocket]] = {}


def load_config() -> dict:
    p = Path("config.yaml")
    if p.exists():
        with open(p) as f:
            return yaml.safe_load(f) or {}
    return {}


def _create_scraper(source: str, engine: ScraperEngine, countries: list[str], max_results: int):
    if source == "europages":
        return EuropagesScraper(engine, countries, max_results)
    elif source == "kompass":
        return KompassScraper(engine, countries, max_results)
    elif source == "wlw":
        return WLWScraper(engine, countries, max_results)
    elif source == "industrystock":
        return IndustryStockScraper(engine, countries, max_results)
    elif source == "paginegialle":
        locations = [c for c in countries if c.lower() not in (
            "italy", "italia", "germany", "france", "spain", "international",
        )] or [""]
        return PagineGialleScraper(engine, locations=locations, max_results=max_results)
    elif source == "csea_energivori":
        return CSEAEnergivoriScraper(engine, max_results=max_results)
    else:
        raise ValueError(f"Unknown source: {source}")


async def _notify(job_id: str, msg: dict):
    for ws in websockets.get(job_id, []):
        try:
            await ws.send_json(msg)
        except Exception:
            pass


async def run_job(job_id: str, params: dict):
    config = load_config()
    rate_cfg = config.get("rate_limit", {})
    proxy_cfg = config.get("proxy", {})
    email_cfg = config.get("email_enrichment", {})

    keywords = params.get("keywords", ["manufacturing"])
    countries = params.get("countries", ["international"])
    max_results = params.get("max_results", 0)
    sources = params.get("sources", ["europages"])
    enrich = params.get("email_enrichment", True)
    fmt = params.get("format", "excel")

    proxies = proxy_cfg.get("list", []) if proxy_cfg.get("enabled") else []

    engine = ScraperEngine(
        proxies=proxies,
        delay_min=rate_cfg.get("delay_min", 1.0),
        delay_max=rate_cfg.get("delay_max", 3.0),
        backoff_base=rate_cfg.get("backoff_base", 5.0),
        max_retries=rate_cfg.get("max_retries", 3),
    )

    jobs[job_id]["status"] = "running"
    all_companies: list[Company] = []

    try:
        for source in sources:
            scraper = _create_scraper(source, engine, countries, max_results)
            for keyword in keywords:
                await _notify(job_id, {
                    "type": "log",
                    "msg": f"[{source}] Searching '{keyword}'..."
                })
                try:
                    async for company in scraper.search(keyword):
                        all_companies.append(company)
                        jobs[job_id]["found"] = len(all_companies)
                        if len(all_companies) % 5 == 0:
                            await _notify(job_id, {
                                "type": "progress",
                                "found": len(all_companies),
                                "source": source,
                                "keyword": keyword,
                            })
                except Exception as e:
                    await _notify(job_id, {
                        "type": "log",
                        "msg": f"[{source}] Error: {e}"
                    })

                await _notify(job_id, {
                    "type": "log",
                    "msg": f"[{source}] '{keyword}' done — {len(all_companies)} total"
                })

        # Email enrichment
        if enrich:
            no_email = [c for c in all_companies if not c.email and c.website]
            if no_email:
                await _notify(job_id, {
                    "type": "log",
                    "msg": f"Enriching {len(no_email)} companies without email..."
                })
                enricher = EmailEnricher(
                    engine,
                    paths=email_cfg.get("paths"),
                    timeout=email_cfg.get("timeout", 10.0),
                )
                for i, company in enumerate(no_email):
                    await enricher.enrich(company)
                    if (i + 1) % 5 == 0:
                        await _notify(job_id, {
                            "type": "log",
                            "msg": f"Enriched {i + 1}/{len(no_email)}"
                        })

        await engine.close()

        # Clean + export
        raw_count = len(all_companies)
        all_companies = clean_companies(all_companies)

        await _notify(job_id, {
            "type": "log",
            "msg": f"Cleaned: {raw_count} → {len(all_companies)} companies"
        })

        if all_companies:
            filepath = export(all_companies, fmt, "./output")
            with_email = sum(1 for c in all_companies if c.email)
            with_phone = sum(1 for c in all_companies if c.phone)

            jobs[job_id].update({
                "status": "done",
                "found": len(all_companies),
                "with_email": with_email,
                "with_phone": with_phone,
                "file": filepath,
                "requests": engine.request_count,
                "companies": [c.to_dict() for c in all_companies[:500]],
            })
            await _notify(job_id, {"type": "done", "job": jobs[job_id]})
        else:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["found"] = 0
            await _notify(job_id, {"type": "done", "job": jobs[job_id]})

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        await _notify(job_id, {"type": "error", "msg": str(e)})
        logger.exception(f"Job {job_id} failed")
        await engine.close()


# --- FastAPI ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    Path("output").mkdir(exist_ok=True)
    yield

app = FastAPI(title="ScraperUniversale", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.post("/api/jobs")
async def create_job(params: dict):
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "found": 0,
        "params": params,
        "created": datetime.now().isoformat(),
    }
    asyncio.create_task(run_job(job_id, params))
    return {"job_id": job_id}


@app.get("/api/jobs")
async def list_jobs():
    safe = []
    for j in jobs.values():
        entry = {k: v for k, v in j.items() if k != "companies"}
        safe.append(entry)
    return safe


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in jobs:
        return JSONResponse({"error": "not found"}, 404)
    return jobs[job_id]


@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str):
    job = jobs.get(job_id)
    if not job or "file" not in job:
        return JSONResponse({"error": "no file"}, 404)
    return FileResponse(job["file"], filename=os.path.basename(job["file"]))


@app.get("/api/files")
async def list_files():
    out = Path("output")
    files = []
    if out.exists():
        for f in sorted(out.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
    return files


@app.get("/api/files/{filename}")
async def download_file(filename: str):
    filepath = Path("output") / filename
    if not filepath.exists() or not filepath.is_file():
        return JSONResponse({"error": "not found"}, 404)
    return FileResponse(str(filepath), filename=filename)


@app.websocket("/ws/{job_id}")
async def ws_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()
    websockets.setdefault(job_id, []).append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        websockets.get(job_id, []).remove(websocket)


# --- HTML ---

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ScraperUniversale</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap');

* { margin:0; padding:0; box-sizing:border-box; }

:root {
  --bg: #0a0a0f;
  --bg2: #12121a;
  --bg3: #1a1a2e;
  --surface: #16213e;
  --border: #0f3460;
  --green: #00ff41;
  --green2: #00cc33;
  --green-dim: #00ff4120;
  --cyan: #00d4ff;
  --cyan-dim: #00d4ff15;
  --purple: #a855f7;
  --pink: #ec4899;
  --yellow: #fbbf24;
  --red: #ef4444;
  --text: #e2e8f0;
  --text-dim: #64748b;
  --text-bright: #f8fafc;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', sans-serif;
  min-height: 100vh;
  overflow-x: hidden;
}

/* Matrix rain background */
#matrix-bg {
  position: fixed; top:0; left:0; width:100%; height:100%;
  z-index: 0; opacity: 0.04; pointer-events: none;
}

.app { position: relative; z-index: 1; max-width: 1400px; margin: 0 auto; padding: 20px; }

/* Header */
.header {
  text-align: center;
  padding: 40px 0 30px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 30px;
}
.header h1 {
  font-family: 'JetBrains Mono', monospace;
  font-size: 2.4rem;
  font-weight: 700;
  background: linear-gradient(135deg, var(--green), var(--cyan));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: -0.5px;
}
.header .tagline {
  color: var(--text-dim);
  font-size: 0.9rem;
  margin-top: 8px;
  font-family: 'JetBrains Mono', monospace;
}
.header .status-bar {
  display: flex;
  justify-content: center;
  gap: 24px;
  margin-top: 16px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: var(--text-dim);
}
.header .status-bar .dot {
  display: inline-block;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--green);
  margin-right: 6px;
  animation: pulse 2s infinite;
  vertical-align: middle;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* Grid layout */
.grid {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 24px;
  align-items: start;
}
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr; }
}

/* Cards */
.card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  position: relative;
  overflow: hidden;
}
.card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--green), var(--cyan), var(--purple));
}
.card h2 {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 2px;
  color: var(--green);
  margin-bottom: 20px;
}

/* Form */
label {
  display: block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: var(--cyan);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 1px;
}
input[type="text"], input[type="number"], select {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  color: var(--text-bright);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  margin-bottom: 16px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
input:focus, select:focus {
  outline: none;
  border-color: var(--green);
  box-shadow: 0 0 0 3px var(--green-dim);
}
select { cursor: pointer; }
select option { background: var(--bg2); color: var(--text); }

/* Source chips */
.sources-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}
.source-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 20px;
  border: 1px solid var(--border);
  background: var(--bg);
  cursor: pointer;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  color: var(--text-dim);
  transition: all 0.2s;
  user-select: none;
}
.source-chip:hover { border-color: var(--cyan); color: var(--text); }
.source-chip.active {
  border-color: var(--green);
  background: var(--green-dim);
  color: var(--green);
}
.source-chip input { display: none; }

/* Toggle */
.toggle-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.toggle {
  width: 40px; height: 22px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 11px;
  cursor: pointer;
  position: relative;
  transition: all 0.3s;
}
.toggle.on { background: var(--green-dim); border-color: var(--green); }
.toggle::after {
  content: '';
  position: absolute;
  top: 2px; left: 2px;
  width: 16px; height: 16px;
  border-radius: 50%;
  background: var(--text-dim);
  transition: all 0.3s;
}
.toggle.on::after { left: 20px; background: var(--green); }

/* Launch button */
.btn-launch {
  width: 100%;
  padding: 14px;
  background: linear-gradient(135deg, #00ff41, #00cc33);
  border: none;
  border-radius: 10px;
  color: #000;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 0.95rem;
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 2px;
  transition: all 0.3s;
  position: relative;
  overflow: hidden;
}
.btn-launch:hover {
  transform: translateY(-1px);
  box-shadow: 0 0 30px var(--green-dim);
}
.btn-launch:active { transform: translateY(0); }
.btn-launch:disabled {
  background: var(--bg3);
  color: var(--text-dim);
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

/* Right column */
.right-col { display: flex; flex-direction: column; gap: 24px; }

/* Terminal / Log */
.terminal {
  background: #000;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  line-height: 1.7;
  max-height: 300px;
  overflow-y: auto;
  color: var(--green);
}
.terminal .log-line { opacity: 0; animation: fadeIn 0.3s forwards; }
@keyframes fadeIn { to { opacity: 1; } }
.terminal .log-error { color: var(--red); }
.terminal .log-info { color: var(--cyan); }
.terminal .log-success { color: var(--green); }
.terminal .prompt { color: var(--text-dim); user-select: none; }

/* Stats cards */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
@media (max-width: 700px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
}
.stat-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  text-align: center;
}
.stat-card .stat-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--green);
}
.stat-card .stat-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-dim);
  margin-top: 4px;
}
.stat-card:nth-child(2) .stat-value { color: var(--cyan); }
.stat-card:nth-child(3) .stat-value { color: var(--purple); }
.stat-card:nth-child(4) .stat-value { color: var(--yellow); }

/* Results table */
.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}
th {
  background: var(--bg3);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--cyan);
  padding: 12px 14px;
  text-align: left;
  position: sticky;
  top: 0;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 10px 14px;
  border-bottom: 1px solid #ffffff08;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
tr:hover td { background: var(--cyan-dim); }
a { color: var(--cyan); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Files section */
.file-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 8px;
  background: var(--bg);
  transition: border-color 0.2s;
}
.file-item:hover { border-color: var(--cyan); }
.file-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem;
  color: var(--text-bright);
}
.file-meta {
  font-size: 0.7rem;
  color: var(--text-dim);
}
.btn-dl {
  padding: 6px 14px;
  background: transparent;
  border: 1px solid var(--cyan);
  border-radius: 6px;
  color: var(--cyan);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-dl:hover { background: var(--cyan); color: #000; }

/* Empty state */
.empty {
  text-align: center;
  padding: 40px;
  color: var(--text-dim);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
}
.empty .icon { font-size: 2rem; margin-bottom: 12px; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--green); }
</style>
</head>
<body>

<canvas id="matrix-bg"></canvas>

<div class="app">
  <div class="header">
    <h1>&gt;_ ScraperUniversale</h1>
    <div class="tagline">Enterprise B2B Company Data Extraction</div>
    <div class="status-bar">
      <span><span class="dot"></span>SYSTEM ONLINE</span>
      <span>6 SOURCES</span>
      <span>v1.0</span>
    </div>
  </div>

  <!-- Stats -->
  <div class="stats-row" style="margin-bottom:24px;">
    <div class="stat-card"><div class="stat-value" id="stat-found">0</div><div class="stat-label">Companies Found</div></div>
    <div class="stat-card"><div class="stat-value" id="stat-email">0</div><div class="stat-label">With Email</div></div>
    <div class="stat-card"><div class="stat-value" id="stat-phone">0</div><div class="stat-label">With Phone</div></div>
    <div class="stat-card"><div class="stat-value" id="stat-requests">0</div><div class="stat-label">HTTP Requests</div></div>
  </div>

  <div class="grid">
    <!-- Left: Config Panel -->
    <div class="card">
      <h2>// Launch Scraper</h2>

      <label>Keywords</label>
      <input type="text" id="keywords" placeholder="steel, manufacturing, energy" value="manufacturing">

      <label>Countries</label>
      <input type="text" id="countries" placeholder="Italy, Germany, France" value="Italy, Germany">

      <label>Sources</label>
      <div class="sources-grid">
        <label class="source-chip active"><input type="checkbox" value="europages" checked>Europages</label>
        <label class="source-chip"><input type="checkbox" value="kompass">Kompass</label>
        <label class="source-chip"><input type="checkbox" value="wlw">wlw.de</label>
        <label class="source-chip"><input type="checkbox" value="industrystock">IndustryStock</label>
        <label class="source-chip"><input type="checkbox" value="paginegialle">PagineGialle</label>
        <label class="source-chip"><input type="checkbox" value="csea_energivori">CSEA</label>
      </div>

      <label>Max Results (per source)</label>
      <input type="number" id="max-results" value="50" min="0" placeholder="0 = unlimited">

      <label>Export Format</label>
      <select id="format">
        <option value="excel">Excel (.xlsx)</option>
        <option value="csv">CSV</option>
        <option value="json">JSON</option>
      </select>

      <div class="toggle-row">
        <label style="margin:0">Email Enrichment</label>
        <div class="toggle on" id="enrich-toggle"></div>
      </div>

      <button class="btn-launch" id="btn-launch" onclick="launch()">
        LAUNCH SCRAPER
      </button>
    </div>

    <!-- Right: Terminal + Results -->
    <div class="right-col">
      <div class="card">
        <h2>// Terminal</h2>
        <div class="terminal" id="terminal">
          <div class="log-line"><span class="prompt">$</span> Ready. Configure and launch.</div>
        </div>
      </div>

      <div class="card" id="results-card" style="display:none;">
        <h2>// Results</h2>
        <div class="table-wrap" style="max-height:400px; overflow-y:auto;">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Company</th>
                <th>City</th>
                <th>Country</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Website</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody id="results-body"></tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2>// Downloads</h2>
        <div id="files-list"><div class="empty"><div class="icon">//</div>No exports yet</div></div>
      </div>
    </div>
  </div>
</div>

<script>
// Matrix rain
const canvas = document.getElementById('matrix-bg');
const ctx = canvas.getContext('2d');
canvas.width = window.innerWidth;
canvas.height = window.innerHeight;
const chars = 'アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン0123456789';
const fontSize = 14;
const columns = Math.floor(canvas.width / fontSize);
const drops = Array(columns).fill(1);
function drawMatrix() {
  ctx.fillStyle = 'rgba(10,10,15,0.05)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#00ff41';
  ctx.font = fontSize + 'px monospace';
  for (let i = 0; i < drops.length; i++) {
    const text = chars[Math.floor(Math.random() * chars.length)];
    ctx.fillText(text, i * fontSize, drops[i] * fontSize);
    if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) drops[i] = 0;
    drops[i]++;
  }
}
setInterval(drawMatrix, 50);
window.addEventListener('resize', () => { canvas.width = window.innerWidth; canvas.height = window.innerHeight; });

// Source chips toggle
document.querySelectorAll('.source-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const cb = chip.querySelector('input');
    cb.checked = !cb.checked;
    chip.classList.toggle('active', cb.checked);
  });
});

// Enrich toggle
const enrichToggle = document.getElementById('enrich-toggle');
enrichToggle.addEventListener('click', () => enrichToggle.classList.toggle('on'));

// Terminal logging
const terminal = document.getElementById('terminal');
function log(msg, cls = '') {
  const line = document.createElement('div');
  line.className = 'log-line ' + cls;
  const now = new Date().toLocaleTimeString();
  line.innerHTML = `<span class="prompt">[${now}]</span> ${msg}`;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
}

// Stats
function setStat(id, val) {
  const el = document.getElementById(id);
  el.textContent = typeof val === 'number' ? val.toLocaleString() : val;
}

// Load files
async function loadFiles() {
  try {
    const res = await fetch('/api/files');
    const files = await res.json();
    const container = document.getElementById('files-list');
    if (!files.length) {
      container.innerHTML = '<div class="empty"><div class="icon">//</div>No exports yet</div>';
      return;
    }
    container.innerHTML = files.map(f => `
      <div class="file-item">
        <div>
          <div class="file-name">${f.name}</div>
          <div class="file-meta">${(f.size / 1024).toFixed(1)} KB &mdash; ${new Date(f.modified).toLocaleString()}</div>
        </div>
        <button class="btn-dl" onclick="window.location.href='/api/files/${f.name}'">DOWNLOAD</button>
      </div>
    `).join('');
  } catch(e) {}
}
loadFiles();

// Launch
let currentJobId = null;
async function launch() {
  const btn = document.getElementById('btn-launch');
  btn.disabled = true;
  btn.textContent = 'RUNNING...';

  const keywords = document.getElementById('keywords').value.split(',').map(s => s.trim()).filter(Boolean);
  const countries = document.getElementById('countries').value.split(',').map(s => s.trim()).filter(Boolean);
  const sources = [...document.querySelectorAll('.source-chip input:checked')].map(cb => cb.value);
  const maxResults = parseInt(document.getElementById('max-results').value) || 0;
  const format = document.getElementById('format').value;
  const enrich = enrichToggle.classList.contains('on');

  if (!sources.length) {
    log('ERROR: Select at least one source!', 'log-error');
    btn.disabled = false;
    btn.textContent = 'LAUNCH SCRAPER';
    return;
  }

  log(`Launching scrape: ${keywords.join(', ')} | ${countries.join(', ')} | ${sources.join(', ')}`, 'log-info');

  setStat('stat-found', 0);
  setStat('stat-email', 0);
  setStat('stat-phone', 0);
  setStat('stat-requests', 0);

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ keywords, countries, sources, max_results: maxResults, format, email_enrichment: enrich }),
    });
    const data = await res.json();
    currentJobId = data.job_id;
    log(`Job started: ${currentJobId}`, 'log-success');

    // WebSocket for live updates
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/${currentJobId}`);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'log') {
        log(msg.msg, msg.msg.includes('Error') ? 'log-error' : 'log-info');
      } else if (msg.type === 'progress') {
        setStat('stat-found', msg.found);
        log(`Found ${msg.found} companies...`, 'log-success');
      } else if (msg.type === 'done') {
        const job = msg.job;
        setStat('stat-found', job.found || 0);
        setStat('stat-email', job.with_email || 0);
        setStat('stat-phone', job.with_phone || 0);
        setStat('stat-requests', job.requests || 0);
        log(`DONE! ${job.found} companies extracted.`, 'log-success');
        if (job.companies && job.companies.length) showResults(job.companies);
        loadFiles();
        btn.disabled = false;
        btn.textContent = 'LAUNCH SCRAPER';
        ws.close();
      } else if (msg.type === 'error') {
        log(`ERROR: ${msg.msg}`, 'log-error');
        btn.disabled = false;
        btn.textContent = 'LAUNCH SCRAPER';
        ws.close();
      }
    };
    ws.onerror = () => {
      log('WebSocket error', 'log-error');
      btn.disabled = false;
      btn.textContent = 'LAUNCH SCRAPER';
    };
  } catch(e) {
    log(`Failed to start: ${e.message}`, 'log-error');
    btn.disabled = false;
    btn.textContent = 'LAUNCH SCRAPER';
  }
}

function showResults(companies) {
  const card = document.getElementById('results-card');
  const tbody = document.getElementById('results-body');
  card.style.display = 'block';
  tbody.innerHTML = companies.map((c, i) => `
    <tr>
      <td style="color:var(--text-dim)">${i+1}</td>
      <td style="color:var(--text-bright);font-weight:500">${esc(c.name)}</td>
      <td>${esc(c.city)}</td>
      <td>${esc(c.country)}</td>
      <td>${c.email ? `<a href="mailto:${esc(c.email)}">${esc(c.email)}</a>` : '<span style="color:var(--text-dim)">—</span>'}</td>
      <td>${esc(c.phone) || '<span style="color:var(--text-dim)">—</span>'}</td>
      <td>${c.website ? `<a href="${esc(c.website)}" target="_blank">${esc(c.website.replace(/https?:\/\//, '').slice(0,30))}</a>` : '<span style="color:var(--text-dim)">—</span>'}</td>
      <td><span style="color:var(--purple)">${esc(c.source)}</span></td>
    </tr>
  `).join('');
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    print("\n  ScraperUniversale Dashboard")
    print("  http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
