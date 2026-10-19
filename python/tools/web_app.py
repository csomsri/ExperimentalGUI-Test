from project_paths import project_file
# web_app.py — local offline website for Vacuum + Beam + SQLite history
# Run with:  uvicorn web_app:app --host 0.0.0.0 --port 8000
# Then open: http://<PC-IP>:8000/  (phone must be on same WiFi/LAN)

import os, sqlite3, json
from datetime import datetime
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DB_PATH = os.environ.get("CNL_SQLITE", str(project_file('cyclotron_data.db')))
TABLE   = "channel_data"
TIME_COL = "timestamp"
LV_EPOCH_OFFSET = 2082844800

# Channels (adjust labels if you like)
VAC_COLS   = ["vac1","vac2","vac3","vac4","vac5"]
BEAM_COL   = "beam_current"
SOURCE_COLS= ["arc_voltage","arc_current","filament","esd_kv","esd_ma","outside_iron","inside_iron"]
TRIM_COLS  = ["ch1","ch2","ch3","ch4","ch5","ch6","ch7","ch8","ch9","ch10","ch11","ch12","main_magnet","centering_beam"]

app = FastAPI(title="CNL Offline Monitor")

# Serve static assets (chart.js) and simple HTML
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), str(project_file('static')))), name="static")

def _to_dt(val: float|str):
    """LabVIEW or Unix or ISO → datetime"""
    try:
        fv = float(val)
        if fv > 3_000_000_000:  # LabVIEW seconds
            fv -= LV_EPOCH_OFFSET
        return datetime.fromtimestamp(fv)
    except Exception:
        return datetime.fromisoformat(str(val).replace("Z",""))

def _cols_present(conn):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({TABLE})")
    return [r[1] for r in cur.fetchall()]

def _select_latest(conn):
    cols = _cols_present(conn)
    sel_cols = [TIME_COL] + [c for c in (VAC_COLS + [BEAM_COL]) if c in cols]
    sql = f"SELECT {', '.join(sel_cols)} FROM {TABLE} ORDER BY ROWID DESC LIMIT 1"
    cur = conn.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    if not row:
        return {}
    ts = _to_dt(row[0])
    payload = {"ts": ts.isoformat(sep=" ", timespec="seconds")}
    for name, val in zip(sel_cols[1:], row[1:]):
        payload[name] = None if val is None else float(val)
    return payload

def _select_range(conn, start_iso, end_iso, cols):
    cur = conn.cursor()
    # detect ts kind from a sample
    cur.execute(f"SELECT {TIME_COL} FROM {TABLE} ORDER BY ROWID LIMIT 1")
    sample = cur.fetchone()
    ts_kind = "text"
    if sample:
        try:
            fv = float(sample[0])
            ts_kind = "labview" if fv > 3_000_000_000 else "numeric"
        except Exception:
            ts_kind = "text"

    fields = [TIME_COL] + cols
    where = ""
    params = []
    if ts_kind == "numeric":
        where = f"WHERE {TIME_COL} >= ? AND {TIME_COL} <= ?"
        params = [datetime.fromisoformat(start_iso).timestamp(),
                  datetime.fromisoformat(end_iso).timestamp()]
    elif ts_kind == "labview":
        where = f"WHERE {TIME_COL} >= ? AND {TIME_COL} <= ?"
        params = [datetime.fromisoformat(start_iso).timestamp() + LV_EPOCH_OFFSET,
                  datetime.fromisoformat(end_iso).timestamp() + LV_EPOCH_OFFSET]
    else:
        where = f"WHERE {TIME_COL} >= ? AND {TIME_COL} <= ?"
        params = [start_iso, end_iso]

    sql = f"SELECT {', '.join(fields)} FROM {TABLE} {where} ORDER BY {TIME_COL}"
    cur.execute(sql, params)
    rows = cur.fetchall()
    out = {"ts": [], "data": {c: [] for c in cols}}
    for r in rows:
        dt = _to_dt(r[0])
        out["ts"].append(dt.isoformat(sep=" ", timespec="seconds"))
        for c, v in zip(cols, r[1:]):
            out["data"][c].append(None if v is None else float(v))
    return out

@app.get("/", response_class=HTMLResponse)
def index():
    html = """
<!doctype html><html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CNL Vacuum & Beam — Offline</title>
<style>
body{font-family:system-ui,Segoe UI,Arial;color:#e6f3ff;background:#0b0f14;margin:0}
header{padding:10px 14px;background:#0e141b;border-bottom:1px solid #1c2a3a}
h1{font-size:18px;margin:0}
.container{padding:12px}
.card{background:#0e141b;border:1px solid #1c2a3a;border-radius:10px;padding:12px;margin-bottom:12px}
.row{display:flex;gap:12px;flex-wrap:wrap}
.col{flex:1;min-width:300px}
.label{color:#9cc9ff;font-size:12px}
.value{font-size:24px;margin-top:4px}
canvas{background:#0d0f15;border:1px solid #22384f;border-radius:8px}
input,select,button{background:#111821;color:#e6f3ff;border:1px solid #2b92f9;border-radius:6px;padding:6px}
button{background:#0e5aa6;border:1px solid #67c2ff}
button:hover{background:#1299c8}
</style>
</head><body>
<header><h1>CNL — Vacuum & Beam (offline)</h1></header>
<div class="container">
  <div class="row">
    <div class="card col">
      <div class="label">Last update</div>
      <div id="lastts" class="value">—</div>
    </div>
    <div class="card col">
      <div class="label">Beam Current</div>
      <div id="beamv" class="value">—</div>
    </div>
    <div class="card col">
      <div class="label">Vacuum (1–5)</div>
      <div id="vacv" class="value">—</div>
    </div>
  </div>
  <div class="row">
    <div class="card col"><canvas id="vacChart" height="200"></canvas></div>
    <div class="card col"><canvas id="beamChart" height="200"></canvas></div>
  </div>

  <div class="card">
    <div class="row" style="align-items:center;gap:8px">
      <span class="label">Data Explorer</span>
      <label>Start <input type="datetime-local" id="start"></label>
      <label>End <input type="datetime-local" id="end"></label>
      <select id="cols" multiple size="6" style="min-width:240px">
        <option>vac1</option><option>vac2</option><option>vac3</option><option>vac4</option><option>vac5</option>
        <option>beam_current</option>
        <option>arc_voltage</option><option>arc_current</option><option>filament</option>
        <option>esd_kv</option><option>esd_ma</option><option>outside_iron</option><option>inside_iron</option>
        <option>ch1</option><option>ch2</option><option>ch3</option><option>ch4</option><option>ch5</option><option>ch6</option>
        <option>ch7</option><option>ch8</option><option>ch9</option><option>ch10</option><option>ch11</option><option>ch12</option>
        <option>main_magnet</option><option>centering_beam</option><option>latency</option>
      </select>
      <button id="queryBtn">Query & Plot</button>
    </div>
    <div style="margin-top:10px"><canvas id="exploreChart" height="220"></canvas></div>
  </div>
</div>

<script src="/static/chart.umd.min.js"></script>
<script>
const vacCtx   = document.getElementById('vacChart').getContext('2d');
const beamCtx  = document.getElementById('beamChart').getContext('2d');
const exCtx    = document.getElementById('exploreChart').getContext('2d');

function newLineChart(ctx, label){
  return new Chart(ctx,{type:'line',data:{labels:[],datasets:[]},
  options:{animation:false,responsive:true,maintainAspectRatio:false,
    scales:{x:{ticks:{color:'#9cc9ff'}},y:{ticks:{color:'#9cc9ff'}}},
    plugins:{legend:{labels:{color:'#cfe8ff'}}}}});
}
const vacChart = newLineChart(vacCtx,"Vacuum");     // 5 lines
const beamChart= newLineChart(beamCtx,"Beam");      // 1 line
const exChart  = newLineChart(exCtx,"Explorer");    // dynamic lines

// colors
const COLORS = ['#5ad','#f77','#6c6','#cc6','#c6f','#888'];

function ensureDatasets(chart, names){
  const existing = new Set(chart.data.datasets.map(d=>d.label));
  names.forEach((n,i)=>{
    if(!existing.has(n)){
      chart.data.datasets.push({label:n, data:[], borderColor:COLORS[i%COLORS.length], tension:0});
    }
  });
}

async function pollLatest(){
  try{
    const r = await fetch('/api/vacuum/latest');
    if(!r.ok) return;
    const d = await r.json();
    if(!d.ts) return;
    document.getElementById('lastts').textContent = d.ts;
    const vacs = ['vac1','vac2','vac3','vac4','vac5'].filter(k=>k in d);
    const beam = ('beam_current' in d)? d.beam_current : null;
    document.getElementById('vacv').textContent = vacs.map(k=>d[k]?.toFixed(3)).join('  ');
    document.getElementById('beamv').textContent = beam!=null ? beam.toFixed(3) : '—';

    // update charts
    ensureDatasets(vacChart, vacs);
    if(vacChart.data.labels.length>600){ vacChart.data.labels.shift(); vacChart.data.datasets.forEach(ds=>ds.data.shift()); }
    vacChart.data.labels.push(d.ts);
    vacChart.data.datasets.forEach(ds => ds.data.push(d[ds.label]));
    vacChart.update('none');

    ensureDatasets(beamChart, ['beam_current']);
    if(beamChart.data.labels.length>600){ beamChart.data.labels.shift(); beamChart.data.datasets.forEach(ds=>ds.data.shift()); }
    beamChart.data.labels.push(d.ts);
    beamChart.data.datasets[0].data.push(beam);
    beamChart.update('none');

  }catch(e){ /* ignore */ }
}
setInterval(pollLatest, 1000);
pollLatest();

// Explorer
document.getElementById('queryBtn').onclick = async ()=>{
  const s = document.getElementById('start').value;
  const e = document.getElementById('end').value;
  const colsSel = document.getElementById('cols');
  const cols = Array.from(colsSel.selectedOptions).map(o=>o.value);
  if(!s || !e || cols.length==0){ alert('Pick start/end and at least one column'); return; }
  const qs = new URLSearchParams({start:s.replace('T',' '), end:e.replace('T',' '), cols:cols.join(',')});
  const r = await fetch('/api/query?'+qs.toString());
  if(!r.ok){ alert('Query error'); return; }
  const d = await r.json();
  exChart.data.labels = d.ts;
  exChart.data.datasets = [];
  const names = Object.keys(d.data);
  names.forEach((n,i)=>{
    exChart.data.datasets.push({label:n, data:d.data[n], borderColor:COLORS[i%COLORS.length], tension:0});
  });
  exChart.update();
};
</script>
</body></html>
"""
    return HTMLResponse(html)

@app.get("/db", response_class=HTMLResponse)
def db_page():
    # Simple redirect to / (Explorer is included there)
    return HTMLResponse('<meta http-equiv="refresh" content="0; url=/" />')

@app.get("/api/vacuum/latest")
def api_latest():
    if not os.path.exists(DB_PATH):
        return JSONResponse({})
    with sqlite3.connect(DB_PATH) as conn:
        return JSONResponse(_select_latest(conn))

@app.get("/api/query")
def api_query(start: str = Query(..., description="YYYY-MM-DD HH:MM:SS"),
              end: str   = Query(..., description="YYYY-MM-DD HH:MM:SS"),
              cols: str  = Query(..., description="comma-separated columns")):
    if not os.path.exists(DB_PATH):
        return JSONResponse({"ts":[], "data":{} })
    user_cols = [c.strip() for c in cols.split(",") if c.strip()]
    with sqlite3.connect(DB_PATH) as conn:
        present = set(_cols_present(conn))
        valid = [c for c in user_cols if c in present]
        if not valid:
            return JSONResponse({"ts":[], "data":{} })
        out = _select_range(conn, start, end, valid)
        return JSONResponse(out)
