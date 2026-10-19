"""
app.py — UI lokal sederhana untuk ig_scraper.py.

Jalankan:
    python app.py
lalu buka http://localhost:8000
"""

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import html as htmllib
import sqlite3

from pipeline import CATEGORIES
from report_dashboard import CSS as REPORT_CSS, JS as REPORT_JS, build_html, fmt_num, load_profiles

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8000

JOB = {"proc": None, "log": [], "lock": threading.Lock()}

PAGE = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IG Scraper</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #f8fafc; color: #1e293b; margin: 0; padding: 24px 16px; }
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 16px; }
.panel { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
label { display: block; font-size: 12px; font-weight: 600; color: #475569; margin-bottom: 4px; }
textarea, select, input[type=number] { width: 100%; padding: 8px; border: 1px solid #cbd5e1;
       border-radius: 6px; font: inherit; font-size: 14px; }
textarea { height: 80px; resize: vertical; }
.row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 12px; }
.check { display: flex; align-items: center; gap: 6px; font-size: 13px; margin-top: 12px; }
button { margin-top: 14px; padding: 9px 18px; background: #4f46e5; color: #fff; border: 0;
         border-radius: 6px; font-size: 14px; cursor: pointer; }
button:disabled { background: #94a3b8; cursor: default; }
#status { margin-left: 10px; font-size: 13px; color: #64748b; }
pre { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 6px; font-size: 12px;
      height: 220px; overflow: auto; white-space: pre-wrap; margin: 0; }
iframe { width: 100%; height: 900px; border: 1px solid #e2e8f0; border-radius: 8px; background: #fff; }
.hint { font-size: 12px; color: #94a3b8; margin-top: 6px; }
input[type=text] { width: 100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 6px; font: inherit; font-size: 14px; }
.tabs { display: flex; gap: 6px; margin-bottom: 8px; }
.tab { margin: 0; background: #fff; color: #475569; border: 1px solid #e2e8f0; }
.tab.active { background: #4f46e5; color: #fff; border-color: #4f46e5; }
</style>
</head>
<body>
<div class="wrap">
  <h1>IG Scraper</h1>

  <div class="panel">
    <label style="font-size:15px;margin-bottom:10px">Cari kompetitor di sekitar lokasi</label>
    <div class="row" style="margin-top:0">
      <div>
        <label for="location">Lokasi</label>
        <input id="location" type="text" placeholder="Kemang, Jakarta Selatan">
      </div>
      <div>
        <label for="category">Kategori</label>
        <select id="category">CATEGORY_OPTIONS</select>
      </div>
      <div>
        <label for="radius">Radius (meter)</label>
        <input id="radius" type="number" min="200" max="10000" value="2000">
      </div>
      <div>
        <label for="igscrape">Scrape IG langsung (akun teratas)</label>
        <input id="igscrape" type="number" min="0" max="15" value="3">
      </div>
    </div>
    <button class="runbtn" onclick="runPipeline()">Cari kompetitor</button>
    <div class="hint">Data gratis dari OpenStreetMap + website + Bing + Social Blade + Google Trends. Bisa makan beberapa menit.</div>
  </div>

  <div class="panel">
    <label for="targets">Atau scrape akun IG tertentu — username target (pisahkan dengan koma atau baris baru)</label>
    <textarea id="targets" placeholder="nike, adidas"></textarea>
    <div class="row">
      <div>
        <label for="mode">Mode</label>
        <select id="mode">
          <option value="direct">direct — IP rumah (paling aman)</option>
          <option value="mobile">mobile — hotspot HP</option>
          <option value="webshare">webshare — proxies_webshare.txt</option>
          <option value="custom">custom — proxies.txt</option>
        </select>
      </div>
      <div>
        <label for="maxposts">Maks post per profil</label>
        <input id="maxposts" type="number" min="1" max="100" placeholder="ikut mode">
      </div>
    </div>
    <label class="check"><input type="checkbox" id="force"> Scrape ulang walau baru di-scrape (&lt; 24 jam)</label>
    <button class="runbtn" onclick="run()">Jalankan</button><span id="status"></span>
    <div class="hint">Mode direct: ±15 profil per jam. Lebih dari itu, script menunggu sendiri.</div>
  </div>

  <div class="panel">
    <label>Log</label>
    <pre id="log">Belum ada proses.</pre>
  </div>

  <div class="tabs">
    <button class="tab active" onclick="showTab(this, '/competitors')">Kompetitor lokal</button>
    <button class="tab" onclick="showTab(this, '/report')">Analisis akun IG</button>
  </div>
  <iframe id="report" src="/competitors"></iframe>
</div>
<script>
let wasRunning = false;
let currentTab = '/competitors';
function showTab(btn, path) {
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentTab = path;
  document.getElementById('report').src = path + '?' + Date.now();
}
async function runPipeline() {
  const body = {
    job: 'pipeline',
    location: document.getElementById('location').value,
    category: document.getElementById('category').value,
    radius: document.getElementById('radius').value,
    ig_scrape: document.getElementById('igscrape').value,
  };
  await submit(body);
}
async function run() {
  const body = {
    job: 'ig',
    targets: document.getElementById('targets').value,
    mode: document.getElementById('mode').value,
    max_posts: document.getElementById('maxposts').value,
    force: document.getElementById('force').checked,
  };
  await submit(body);
}
async function submit(body) {
  const r = await fetch('/run', {method: 'POST', body: JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok) { alert(j.error); return; }
  poll();
}
async function poll() {
  const j = await (await fetch('/log')).json();
  const el = document.getElementById('log');
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  el.textContent = j.log || 'Belum ada proses.';
  if (atBottom) el.scrollTop = el.scrollHeight;
  document.querySelectorAll('.runbtn').forEach(b => b.disabled = j.running);
  document.getElementById('status').textContent = j.running ? 'Sedang berjalan…' : '';
  if (wasRunning && !j.running) document.getElementById('report').src = currentTab + '?' + Date.now();
  wasRunning = j.running;
  if (j.running) setTimeout(poll, 1500);
}
poll();
</script>
</body>
</html>"""


def pipeline_cmd(data: dict):
    location = (data.get("location") or "").strip()
    if not location or len(location) > 120:
        return None, "Isi lokasi (maks 120 karakter)."
    category = data.get("category", "cafe")
    if category not in CATEGORIES:
        return None, "Kategori tidak dikenal."
    try:
        radius = min(max(int(data.get("radius") or 2000), 200), 10000)
        ig_scrape = min(max(int(data.get("ig_scrape") or 0), 0), 15)
    except ValueError:
        return None, "Radius / jumlah scrape harus angka."
    return [sys.executable, "-W", "ignore", "-u", "pipeline.py", "--location", location,
            "--category", category, "--radius", str(radius),
            "--ig-scrape", str(ig_scrape)], None


def start_job(data: dict):
    if data.get("job") == "pipeline":
        cmd, err = pipeline_cmd(data)
        if err:
            return err
        return launch(cmd)
    targets =[t.strip().lstrip("@") for t in re.split(r"[,\s]+", data.get("targets", "")) if t.strip()]
    if not targets:
        return "Isi minimal satu username."
    if not all(re.fullmatch(r"[A-Za-z0-9._]{1,30}", t) for t in targets):
        return "Username hanya boleh huruf, angka, titik, dan underscore."
    mode = data.get("mode", "direct")
    if mode not in ("direct", "mobile", "webshare", "custom"):
        return "Mode tidak dikenal."

    cmd = [sys.executable, "-W", "ignore", "-u", "ig_scraper.py",
           "--mode", mode, "--targets", ",".join(targets), "--out", "hasil"]
    mp = str(data.get("max_posts") or "").strip()
    if mp.isdigit():
        cmd += ["--max-posts", mp]
    if data.get("force"):
        cmd += ["--refresh-hours", "0"]
    return launch(cmd)


def launch(cmd):
    with JOB["lock"]:
        if JOB["proc"] and JOB["proc"].poll() is None:
            return "Masih ada proses yang berjalan."
        JOB["log"] = ["$ " + " ".join(cmd[3:]) + "\n"]
        JOB["proc"] = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)

    def reader(proc):
        for line in proc.stdout:
            with JOB["lock"]:
                JOB["log"].append(line)
        proc.wait()
        with JOB["lock"]:
            JOB["log"].append(f"\n[selesai, exit code {proc.returncode}]\n")

    threading.Thread(target=reader, args=(JOB["proc"],), daemon=True).start()
    return None


def competitors_html():
    db = os.path.join(HERE, "ig.db")
    rows = []
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute("SELECT location, data, last_scanned, history FROM businesses").fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
    if not rows:
        return ("<p style='font-family:sans-serif;color:#64748b;padding:16px'>"
                "Belum ada data kompetitor. Isi lokasi di atas lalu klik \"Cari kompetitor\".</p>")
    e = htmllib.escape
    biz = []
    for loc, data, scanned, hist in rows:
        b = json.loads(data)
        b["_loc"], b["_scanned"] = loc, (scanned or "")[:16].replace("T", " ")
        biz.append(b)
    biz.sort(key=lambda b: -(b.get("followers") or 0))
    locs = sorted({b["_loc"] for b in biz})
    with_ig = [b for b in biz if b.get("instagram")]
    cards = [("Bisnis", len(biz)), ("Punya IG", len(with_ig)),
             ("Lokasi dipindai", len(locs)),
             ("Followers terbesar", fmt_num(biz[0].get("followers") or 0))]
    trs = []
    for b in biz:
        ig = b.get("instagram") or ""
        ig_cell = (f'<a href="https://instagram.com/{e(ig)}" target="_blank">@{e(ig)}</a>'
                   + (' <span title="ditemukan via pencarian Bing, cek manual" '
                      'style="color:#d97706">?</span>' if b.get("instagram_source") == "bing" else "")
                   ) if ig else '<span style="color:#cbd5e1">—</span>'
        site = b.get("website") or ""
        site_cell = f'<a href="{e(site)}" target="_blank">web</a>' if site.startswith("http") else ""
        maps = f'https://www.openstreetmap.org/{e(b.get("osm_id", ""))}'
        trend = (b.get("search_trend") or {}).get("trend_pct")
        delta = b.get("followers_delta") or 0
        er = b.get("engagement_rate") or 0
        trs.append(f"""<tr data-type="{e(b['_loc'])}">
          <td><a href="{maps}" target="_blank">{e(b.get('name', ''))}</a>
              <div style="font-size:11px;color:#94a3b8">{e(b.get('address') or '')}</div></td>
          <td>{ig_cell}</td>
          <td data-v="{b.get('followers') or 0}">{fmt_num(b.get('followers') or 0) if ig else ''}</td>
          <td data-v="{delta}" style="color:{'#059669' if delta > 0 else '#dc2626' if delta < 0 else '#94a3b8'}">{('+' if delta > 0 else '') + fmt_num(delta) if delta else ''}</td>
          <td data-v="{er}">{f'{er:.2f}%' if ig and er else ''}</td>
          <td>{e(b.get('grade') or '')}</td>
          <td data-v="{trend if trend is not None else -999}">{'' if trend is None else f'{trend:+.0f}%'}</td>
          <td style="font-size:12px">{site_cell} {e(b.get('phone') or '')}</td>
          <td style="font-size:11px;color:#94a3b8">{e(b['_loc'])}<br>{e(b['_scanned'])}</td>
        </tr>""")
    loc_buttons = "".join(
        f'<button onclick="filterType(this, {e(json.dumps(l))})">{e(l)}</button>' for l in locs)
    heads = "".join(f'<th onclick="sortTable(this)">{h}</th>' for h in
                    ["Bisnis", "Instagram", "Followers", "Δ Followers", "ER", "Grade",
                     "Tren Google", "Kontak", "Lokasi / scan"])
    card_html = "".join(f'<div class="card"><div class="label">{e(str(l))}</div>'
                        f'<div class="value">{e(str(v))}</div></div>' for l, v in cards)
    return f"""<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kompetitor Lokal</title><style>{REPORT_CSS}</style></head><body>
<div class="cards">{card_html}</div>
<div class="filter-bar"><button class="active" onclick="filterType(this, 'ALL')">Semua lokasi</button>{loc_buttons}</div>
<div class="table-wrap"><table><thead><tr>{heads}</tr></thead><tbody>{''.join(trs)}</tbody></table></div>
<p class="sub" style="margin-top:12px">Δ Followers terisi setelah lokasi yang sama dipindai ≥2 kali.
Tanda <span style="color:#d97706">?</span> = akun IG hasil tebakan pencarian, cek manual.
Tren Google = rata-rata 4 minggu terakhir vs 4 minggu sebelumnya.</p>
<script>{REPORT_JS}</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            opts = "".join(f'<option value="{c}">{c}</option>' for c in CATEGORIES)
            self._send(200, PAGE.replace("CATEGORY_OPTIONS", opts))
        elif path == "/competitors":
            self._send(200, competitors_html())
        elif path == "/report":
            profiles = load_profiles(os.path.join(HERE, "ig.db"))
            if profiles:
                self._send(200, build_html(profiles))
            else:
                self._send(200, "<p style='font-family:sans-serif;color:#64748b;padding:16px'>"
                                "Belum ada data. Jalankan scrape dulu.</p>")
        elif path == "/log":
            with JOB["lock"]:
                running = bool(JOB["proc"] and JOB["proc"].poll() is None)
                log = "".join(JOB["log"][-500:])
            self._send(200, json.dumps({"running": running, "log": log}), "application/json")
        else:
            self._send(404, "Not found")

    def do_POST(self):
        if self.path != "/run":
            return self._send(404, "Not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, json.dumps({"error": "Request tidak valid."}), "application/json")
        err = start_job(data)
        if err:
            return self._send(400, json.dumps({"error": err}), "application/json")
        self._send(200, json.dumps({"ok": True}), "application/json")

    def log_message(self, *args):
        pass  # jangan penuhi terminal dengan log request


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"UI jalan di http://localhost:{PORT}  (Ctrl+C untuk berhenti)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
