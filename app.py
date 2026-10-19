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
    <form onsubmit="ask(event)" style="display:flex;gap:8px">
      <input id="prompt" type="text" autofocus autocomplete="off"
             placeholder="PROMPT_PLACEHOLDER"
             style="flex:1;font-size:16px;padding:12px">
      <button class="runbtn" type="submit" style="margin:0">Cari</button>
    </form>
    <div id="understood" class="hint">Contoh: "kompetitor salon di Dago Bandung 3 km" · "cariin kopi dekat Malioboro Jogja" · "resto di Kemang scrape 5 ig"</div>
  </div>

  <details class="panel">
  <summary style="cursor:pointer;font-weight:600;font-size:13px;color:#475569">Mode manual (form)</summary>
  <div style="margin-top:12px">
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
  </details>

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
async function ask(ev) {
  ev.preventDefault();
  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) return;
  const r = await fetch('/ask', {method: 'POST', body: JSON.stringify({prompt})});
  const j = await r.json();
  const u = document.getElementById('understood');
  if (!r.ok) { u.innerHTML = '<span style="color:#dc2626">' + j.error + '</span>'; return; }
  if (j.agent) {
    u.innerHTML = '<span style="color:#059669">Agent sedang bekerja…</span> jawaban muncul di bagian Log.';
    poll(); return;
  }
  const p = j.parsed;
  u.innerHTML = '<span style="color:#059669">Dipahami:</span> <b>' + p.category + '</b> di <b>' +
    p.location + '</b> · radius ' + (p.radius >= 1000 ? (p.radius/1000) + ' km' : p.radius + ' m') +
    ' · scrape IG ' + p.ig_scrape + ' akun teratas';
  document.querySelectorAll('.tab')[0].click();
  poll();
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


# kata kunci -> kategori pipeline (dicek berurutan, frasa panjang dulu)
CATEGORY_WORDS = [
    ("coworking", ["coworking", "co-working", "ruang kerja"]),
    ("fast_food", ["fast food", "fastfood", "burger", "ayam goreng", "fried chicken"]),
    ("bakery", ["bakery", "toko roti", "roti", "toko kue", "kue", "pastry"]),
    ("cafe", ["coffee shop", "kedai kopi", "warung kopi", "coffee", "kopi", "cafe", "kafe", "café"]),
    ("restaurant", ["rumah makan", "warung makan", "restoran", "restaurant", "resto", "makan"]),
    ("clothes", ["fashion", "pakaian", "baju", "butik", "boutique", "distro", "clothing"]),
    ("salon", ["barbershop", "barber", "potong rambut", "pangkas", "salon"]),
    ("beauty", ["skincare", "kosmetik", "kecantikan", "beauty", "klinik kecantikan"]),
    ("gym", ["fitness", "gym", "pusat kebugaran"]),
    ("laundry", ["laundry", "binatu", "cuci baju"]),
    ("pharmacy", ["apotek", "apotik", "farmasi", "pharmacy"]),
    ("hotel", ["penginapan", "hotel", "hostel", "guest house", "homestay"]),
    ("shop", ["toko", "shop", "minimarket"]),
]
FILLER = r"\b(tolong|coba|dong|ya|yang|ada|semua|kompetitor|pesaing|bisnis|usaha|cari(?:in|kan)?|carikan|list|daftar|apa saja|apa aja|scrape|ig|instagram)\b"


def parse_prompt(text: str) -> dict:
    """'cariin cafe di Kemang radius 2 km' -> {category, location, radius, ig_scrape}"""
    t = " " + text.lower().strip() + " "
    out = {"radius": 2000, "ig_scrape": 3}

    m = re.search(r"(?:radius|jarak|dalam)?\s*(\d+(?:[.,]\d+)?)\s*(km|kilo(?:meter)?|meter|m)\b", t)
    if m:
        val = float(m.group(1).replace(",", "."))
        out["radius"] = int(val * 1000 if m.group(2).startswith("k") else val)
        t = t.replace(m.group(0), " ")
    m = re.search(r"(?:scrape|ig|instagram)\s*(\d+)|(\d+)\s*(?:akun|ig)", t)
    if m:
        out["ig_scrape"] = int(m.group(1) or m.group(2))
        t = t.replace(m.group(0), " ")

    for cat, words in CATEGORY_WORDS:
        hit = next((w for w in words if re.search(rf"\b{re.escape(w)}\b", t)), None)
        if hit:
            out["category"] = cat
            t = re.sub(rf"\b{re.escape(hit)}\b", " ", t, count=1)
            break

    m = re.search(r"\b(?:di|sekitar|dekat|daerah|area|kawasan|wilayah)\b\s+(.+)", t)
    loc = m.group(1) if m else t
    loc = re.sub(FILLER, " ", loc)
    loc = re.sub(r"\b(radius|jarak|dalam|sekitar|dekat|di)\b", " ", loc)
    loc = re.sub(r"[^\w\s,.-]", " ", loc)
    out["location"] = re.sub(r"\s+", " ", loc).strip(" ,.-").title()
    return out


OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
# model gratis di OpenRouter berakhiran ":free"; ganti lewat env kalau sudah tidak tersedia
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")


def llm_parse(text: str):
    """Pahami prompt pakai LLM via OpenRouter. Return None kalau tidak ada key / gagal."""
    if not OPENROUTER_KEY:
        return None
    import netutil
    system = (
        "Ubah permintaan user jadi JSON saja, tanpa teks lain. Kunci: "
        f'"category" (salah satu: {", ".join(CATEGORIES)}; null kalau tidak disebut), '
        '"location" (nama tempat di Indonesia, lengkap dengan kota kalau disebut; null kalau tidak ada), '
        '"radius" (meter, default 2000), "ig_scrape" (jumlah akun IG, default 3).')
    try:
        r = netutil.post("https://openrouter.ai/api/v1/chat/completions", retries=2, timeout=30,
                         headers={"Authorization": f"Bearer {OPENROUTER_KEY}",
                                  "Content-Type": "application/json"},
                         json={"model": OPENROUTER_MODEL, "temperature": 0,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": text}]})
        content = r.json()["choices"][0]["message"]["content"]
        j = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
        out = {"radius": int(j.get("radius") or 2000), "ig_scrape": int(j.get("ig_scrape") or 3),
               "location": (j.get("location") or "").strip(), "parser": "llm"}
        if j.get("category") in CATEGORIES:
            out["category"] = j["category"]
        return out
    except Exception as e:
        print(f"[OpenRouter] gagal, pakai parser kata kunci: {e}")
        return None


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


# ------------------------------------------------------------
# CHAT (agent)
# ------------------------------------------------------------

CHAT = {"sessions": {}, "jobs": {}, "lock": threading.Lock(), "seq": 0}


def chat_start(sid: str, message: str):
    import agent
    with CHAT["lock"]:
        sess = CHAT["sessions"].setdefault(sid, {"history": None, "busy": False})
        if sess["busy"]:
            return None, "Agent masih menjawab pesan sebelumnya."
        sess["busy"] = True
        CHAT["seq"] += 1
        jid = str(CHAT["seq"])
        job = CHAT["jobs"][jid] = {"steps": [], "done": False, "answer": "", "error": ""}

    def step(s):
        with CHAT["lock"]:
            job["steps"].append(s)

    def work():
        try:
            answer, history = agent.run_agent(message, sess["history"], log=step)
            if len(history) > 60:  # potong di awal pesan user supaya pasangan tool_call/tool utuh
                tail = history[-40:]
                while tail and tail[0]["role"] != "user":
                    tail.pop(0)
                history = [history[0]] + tail
            sess["history"] = history
            job["answer"] = answer
        except Exception as e:
            job["error"] = str(e)
        finally:
            job["done"] = True
            sess["busy"] = False

    threading.Thread(target=work, daemon=True).start()
    return jid, None


CHAT_PAGE = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Riset Kompetitor</title>
<style>
:root { --bg:#f8fafc; --panel:#fff; --line:#e2e8f0; --text:#1e293b; --muted:#64748b; --accent:#4f46e5; --me:#eef2ff; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f172a; --panel:#1e293b; --line:#334155;
  --text:#e2e8f0; --muted:#94a3b8; --accent:#818cf8; --me:#312e81; } }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--text); display: flex; flex-direction: column; }
header { display: flex; align-items: center; gap: 16px; padding: 12px 16px;
         border-bottom: 1px solid var(--line); background: var(--panel); }
header h1 { font-size: 16px; margin: 0; flex: 1; }
header a { color: var(--muted); font-size: 13px; text-decoration: none; }
header a:hover { color: var(--accent); }
#msgs { flex: 1; overflow-y: auto; padding: 24px 16px; }
.wrap { max-width: 820px; margin: 0 auto; }
.msg { margin-bottom: 18px; line-height: 1.55; font-size: 15px; }
.msg.me { background: var(--me); padding: 10px 14px; border-radius: 12px; margin-left: auto;
          width: fit-content; max-width: 85%; white-space: pre-wrap; }
.msg.bot { overflow-x: auto; }
.msg.bot p { margin: 0 0 10px; }
.msg.bot table { border-collapse: collapse; margin: 8px 0 12px; font-size: 13px; }
.msg.bot th, .msg.bot td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; }
.msg.bot th { background: var(--panel); }
.msg.bot code { background: var(--panel); border: 1px solid var(--line); padding: 1px 5px; border-radius: 4px; font-size: 13px; }
.msg.bot ul, .msg.bot ol { margin: 0 0 10px; padding-left: 22px; }
.msg.bot h1, .msg.bot h2, .msg.bot h3 { font-size: 15px; margin: 14px 0 6px; }
.steps { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.steps div::before { content: "› "; }
.err { color: #dc2626; }
.typing::after { content: "▍"; animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.empty { color: var(--muted); text-align: center; margin-top: 12vh; }
.empty b { color: var(--text); display: block; font-size: 20px; margin-bottom: 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 18px; }
.chip { border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 999px;
        padding: 7px 12px; font-size: 13px; cursor: pointer; }
.chip:hover { border-color: var(--accent); }
form { border-top: 1px solid var(--line); background: var(--panel); padding: 12px 16px; }
form .wrap { display: flex; gap: 8px; }
textarea { flex: 1; resize: none; height: 46px; max-height: 160px; padding: 12px; font: inherit; font-size: 15px;
           border: 1px solid var(--line); border-radius: 10px; background: var(--bg); color: var(--text); }
button { background: var(--accent); color: #fff; border: 0; border-radius: 10px; padding: 0 18px;
         font-size: 15px; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
</style>
</head>
<body>
<header>
  <h1>Riset Kompetitor</h1>
  <a href="/market"><b>Riset pasar</b></a>
  <a href="/competitors" target="_blank">Data kompetitor</a>
  <a href="/report" target="_blank">Analisis IG</a>
  <a href="/tools">Mode manual</a>
  <a href="#" onclick="newChat();return false">Chat baru</a>
</header>
<div id="msgs"><div class="wrap" id="list">
  <div class="empty" id="empty"><b>Mau riset bisnis apa?</b>
    Tanya bebas — agent akan cari bisnis di lokasi itu, cek IG, website, dan tren Google.
    <div class="chips">
      <span class="chip">kompetitor coffee shop di Kemang siapa aja?</span>
      <span class="chip">riset pasar agency di Gading Serpong</span>
      <span class="chip">salon di Dago Bandung yang paling rame di IG</span>
      <span class="chip">bandingin Kopi Kenangan sama Janji Jiwa</span>
    </div>
  </div>
</div></div>
<form id="form"><div class="wrap">
  <textarea id="input" placeholder="Tulis pertanyaan… (Enter kirim, Shift+Enter baris baru)" autofocus></textarea>
  <button id="send" type="submit">Kirim</button>
</div></form>
<script>
let sid = localStorage.getItem('sid') || (Math.random().toString(36).slice(2));
try { localStorage.setItem('sid', sid); } catch (e) {}
const list = document.getElementById('list'), input = document.getElementById('input'),
      send = document.getElementById('send'), box = document.getElementById('msgs');

function esc(s) { return s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function inline(s) {
  return esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\\*\\*([^*]+)\\*\\*/g, '<b>$1</b>').replace(/(^|[^*])\\*([^*\\n]+)\\*/g, '$1<i>$2</i>')
    .replace(/\\[([^\\]]+)\\]\\((https?:[^)\\s]+)\\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/(^|[\\s(])@([A-Za-z0-9_.]{3,30})/g, '$1<a href="https://instagram.com/$2" target="_blank">@$2</a>');
}
function md(text) {
  const lines = text.split('\\n'); let out = '', i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^\\s*\\|.*\\|\\s*$/.test(l) && i + 1 < lines.length && /^\\s*\\|[\\s:|-]+\\|\\s*$/.test(lines[i+1])) {
      const row = r => r.trim().replace(/^\\||\\|$/g, '').split('|').map(c => c.trim());
      out += '<table><thead><tr>' + row(l).map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>';
      i += 2;
      while (i < lines.length && /^\\s*\\|.*\\|\\s*$/.test(lines[i])) {
        out += '<tr>' + row(lines[i]).map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>'; i++;
      }
      out += '</tbody></table>'; continue;
    }
    if (/^\\s*[-*•]\\s+/.test(l)) {
      out += '<ul>'; while (i < lines.length && /^\\s*[-*•]\\s+/.test(lines[i]))
        out += '<li>' + inline(lines[i++].replace(/^\\s*[-*•]\\s+/, '')) + '</li>';
      out += '</ul>'; continue;
    }
    if (/^\\s*\\d+[.)]\\s+/.test(l)) {
      out += '<ol>'; while (i < lines.length && /^\\s*\\d+[.)]\\s+/.test(lines[i]))
        out += '<li>' + inline(lines[i++].replace(/^\\s*\\d+[.)]\\s+/, '')) + '</li>';
      out += '</ol>'; continue;
    }
    const h = l.match(/^#{1,4}\\s+(.*)/);
    if (h) { out += '<h3>' + inline(h[1]) + '</h3>'; i++; continue; }
    if (l.trim()) out += '<p>' + inline(l) + '</p>';
    i++;
  }
  return out;
}
const TOOL_LABEL = {search_competitors: 'Mencari bisnis di lokasi (bisa beberapa menit)…',
  list_competitors: 'Membaca data tersimpan…', scrape_instagram: 'Scrape Instagram…',
  google_trends: 'Cek Google Trends…'};
function stepText(s) {
  const m = s.match(/^🔧 (\\w+)\\((.*)\\)$/);
  if (!m) return s;
  let args = ''; try { args = Object.values(JSON.parse(m[2])).filter(v => typeof v !== 'boolean').join(' · '); } catch (e) {}
  return (TOOL_LABEL[m[1]] || m[1]) + (args ? ' (' + args + ')' : '');
}
function add(cls, html) {
  document.getElementById('empty')?.remove();
  const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html;
  list.appendChild(d); box.scrollTop = box.scrollHeight; return d;
}
async function ask(text) {
  text = text.trim(); if (!text) return;
  add('me', esc(text)); input.value = ''; send.disabled = true;
  const bot = add('bot', '<div class="steps"></div><div class="typing"></div>');
  const r = await fetch('/chat', {method: 'POST', body: JSON.stringify({sid, message: text})});
  const j = await r.json();
  if (!r.ok) { bot.innerHTML = '<span class="err">' + esc(j.error) + '</span>'; send.disabled = false; return; }
  const poll = async () => {
    const p = await (await fetch('/chat/poll?job=' + j.job)).json();
    bot.querySelector('.steps').innerHTML = p.steps.map(s => '<div>' + esc(stepText(s)) + '</div>').join('');
    if (p.done) {
      bot.innerHTML = (p.steps.length ? '<div class="steps">' + p.steps.map(s => '<div>' + esc(stepText(s)) + '</div>').join('') + '</div>' : '')
        + (p.error ? '<span class="err">' + esc(p.error) + '</span>' : md(p.answer));
      send.disabled = false; input.focus();
    } else setTimeout(poll, 1200);
    box.scrollTop = box.scrollHeight;
  };
  poll();
}
function newChat() {
  sid = Math.random().toString(36).slice(2);
  try { localStorage.setItem('sid', sid); } catch (e) {}
  location.reload();
}
document.getElementById('form').onsubmit = e => { e.preventDefault(); if (!send.disabled) ask(input.value); };
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!send.disabled) ask(input.value); } });
document.querySelectorAll('.chip').forEach(c => c.onclick = () => ask(c.textContent));
</script>
</body>
</html>"""

MARKET_PAGE = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Riset Pasar</title>
<style>
:root { --bg:#f6f7fb; --panel:#fff; --line:#e6e8ef; --text:#111827; --muted:#6b7280; --accent:#4f46e5;
        --soft:#eef2ff; --good:#059669; --bad:#dc2626; --warn:#d97706; }
@media (prefers-color-scheme: dark) { :root { --bg:#0b1020; --panel:#141a2e; --line:#252d47; --text:#e5e7eb;
  --muted:#9ca3af; --accent:#818cf8; --soft:#1e2445; --good:#34d399; --bad:#f87171; --warn:#fbbf24; } }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
       background: var(--bg); color: var(--text); }
a { color: var(--accent); text-decoration: none; }
header { display: flex; align-items: center; gap: 18px; padding: 14px 20px; background: var(--panel);
         border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
header h1 { font-size: 16px; margin: 0; flex: 1; }
header a { color: var(--muted); font-size: 13px; }
main { max-width: 1120px; margin: 0 auto; padding: 28px 16px 60px; }
.hero { background: var(--panel); border: 1px solid var(--line); border-radius: 16px; padding: 22px; }
.hero h2 { margin: 0 0 4px; font-size: 22px; }
.hero p { margin: 0 0 16px; color: var(--muted); font-size: 14px; }
.search { display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; }
@media (max-width: 640px) { .search { grid-template-columns: 1fr; } }
input { padding: 13px 14px; font: inherit; font-size: 15px; border: 1px solid var(--line); border-radius: 10px;
        background: var(--bg); color: var(--text); width: 100%; }
button { background: var(--accent); color: #fff; border: 0; border-radius: 10px; padding: 0 22px; font-size: 15px;
         font-weight: 600; cursor: pointer; min-height: 46px; }
button:disabled { opacity: .5; cursor: default; }
.progress { margin-top: 12px; font-size: 13px; color: var(--muted); min-height: 18px; }
.recent { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.pill { border: 1px solid var(--line); background: var(--bg); border-radius: 999px; padding: 6px 12px; font-size: 12px;
        cursor: pointer; color: var(--text); }
.pill:hover, .pill.on { border-color: var(--accent); color: var(--accent); }
.title { margin: 30px 0 14px; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.title h2 { margin: 0; font-size: 22px; }
.title span { color: var(--muted); font-size: 13px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
.kpi .l { font-size: 12px; color: var(--muted); }
.kpi .v { font-size: 26px; font-weight: 700; margin-top: 4px; }
.kpi .s { font-size: 12px; color: var(--muted); margin-top: 2px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
@media (max-width: 860px) { .grid2 { grid-template-columns: 1fr; } }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 18px; margin-top: 12px; }
.grid2 .card { margin-top: 0; }
.card h3 { margin: 0 0 12px; font-size: 15px; }
.card h3 small { color: var(--muted); font-weight: 400; font-size: 12px; margin-left: 6px; }
ul.ins { margin: 0; padding-left: 18px; line-height: 1.6; font-size: 14px; }
ul.ins li { margin-bottom: 4px; }
.ai { background: var(--soft); }
.bar { display: grid; grid-template-columns: 110px 1fr 40px; align-items: center; gap: 10px; font-size: 13px; margin: 7px 0; }
.bar .t { height: 10px; border-radius: 6px; background: var(--line); overflow: hidden; }
.bar .f { height: 100%; background: var(--accent); border-radius: 6px; }
.bar .n { text-align: right; color: var(--muted); }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.tab { background: var(--bg); color: var(--text); border: 1px solid var(--line); font-weight: 500; font-size: 13px;
       min-height: 34px; padding: 0 12px; }
.tab.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.tw { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; color: var(--muted); font-weight: 500; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
td { padding: 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
td .sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.star { color: var(--warn); font-weight: 600; white-space: nowrap; }
.tag { display: inline-block; font-size: 11px; padding: 2px 7px; border-radius: 6px; background: var(--soft); color: var(--accent); margin: 2px 4px 2px 0; }
.wa { display: inline-block; background: #22c55e; color: #fff; font-size: 12px; font-weight: 600; padding: 5px 10px; border-radius: 8px; white-space: nowrap; }
.empty { text-align: center; color: var(--muted); padding: 50px 0; }
.note { font-size: 12px; color: var(--muted); margin-top: 10px; }
</style>
</head>
<body>
<header>
  <h1>Riset Pasar</h1>
  <a href="/">Chat</a><a href="/competitors" target="_blank">Data kompetitor</a><a href="/tools">Mode manual</a>
</header>
<main>
  <section class="hero">
    <h2>Riset pasar industri apa?</h2>
    <p>Data publik Google Maps + website bisnis. Tanpa login, gratis. ±2–4 menit.</p>
    <form class="search" id="f">
      <input id="industry" placeholder="Industri, mis. coffee shop / salon / digital agency" required>
      <input id="area" placeholder="Wilayah, mis. Kemang Jakarta" required>
      <button id="go">Riset</button>
    </form>
    <div class="progress" id="prog"></div>
    <div class="recent" id="recent"></div>
  </section>
  <div id="report"><div class="empty">Belum ada laporan dipilih.</div></div>
</main>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = n => n == null ? '–' : n >= 1e6 ? (n/1e6).toFixed(1)+'jt' : n >= 1e3 ? (n/1e3).toFixed(1)+'rb' : String(n);
let current = null, wasRunning = false;

async function loadRecent(selectId) {
  const list = await (await fetch('/market/reports')).json();
  $('#recent').innerHTML = list.map(r => `<span class="pill" data-id="${r.id}">${esc(r.industry)} · ${esc(r.area)}
     <span style="opacity:.6">${esc(r.created.slice(0,10))}</span></span>`).join('');
  document.querySelectorAll('.pill').forEach(p => p.onclick = () => show(p.dataset.id));
  const id = selectId || (list[0] && list[0].id);
  if (id && !current) show(id);
}
async function show(id) {
  current = id;
  document.querySelectorAll('.pill').forEach(p => p.classList.toggle('on', p.dataset.id == id));
  const r = await (await fetch('/market/report?id=' + id)).json();
  render(r);
}
function bars(rows) {
  const mx = Math.max(1, ...rows.map(r => r[1]));
  return rows.map(([k, v]) => `<div class="bar"><span>${esc(k)}</span><div class="t"><div class="f" style="width:${v/mx*100}%"></div></div><span class="n">${v}</span></div>`).join('');
}
function place(p) {
  const links = [p.instagram ? `<a href="https://instagram.com/${esc(p.instagram)}" target="_blank">@${esc(p.instagram)}</a>` : '',
                 p.website ? `<a href="${esc(p.website)}" target="_blank">web</a>` : '',
                 `<a href="${esc(p.maps_url)}" target="_blank">maps</a>`].filter(Boolean).join(' · ');
  return `<b>${esc(p.name)}</b><div class="sub">${esc(p.category || '')}${p.address ? ' · ' + esc(p.address) : ''}</div><div class="sub">${links}</div>`;
}
function table(rows, mode) {
  if (!rows || !rows.length) return '<div class="empty" style="padding:24px">Tidak ada data di kategori ini.</div>';
  const head = mode === 'prospects'
    ? '<tr><th>Bisnis</th><th>Rating</th><th>Review</th><th>Celah digital</th><th>Hubungi</th></tr>'
    : '<tr><th>#</th><th>Bisnis</th><th>Rating</th><th>Review</th><th>Review/bln</th><th>Kontak</th></tr>';
  const body = rows.map((p, i) => mode === 'prospects'
    ? `<tr><td>${place(p)}</td><td class="star">★ ${p.rating ?? '–'}</td><td>${num(p.reviews)}</td>
         <td>${(p.gaps || []).map(g => `<span class="tag">${esc(g)}</span>`).join('')}</td>
         <td>${p.whatsapp ? `<a class="wa" href="${esc(p.whatsapp)}" target="_blank">WhatsApp</a>` : esc(p.phone || '')}</td></tr>`
    : `<tr><td>${i + 1}</td><td>${place(p)}</td><td class="star">★ ${p.rating ?? '–'}</td><td>${num(p.reviews)}</td>
         <td>${p.reviews_per_month != null ? '+' + p.reviews_per_month : '–'}</td>
         <td>${p.whatsapp ? `<a class="wa" href="${esc(p.whatsapp)}" target="_blank">WA</a>` : esc(p.phone || '')}</td></tr>`).join('');
  return `<div class="tw"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}
const TABS = [['leaders', 'Pemimpin pasar'], ['gems', 'Hidden gems'], ['weak', 'Ramai tapi rating rendah'],
              ['growing', 'Tumbuh tercepat'], ['all', 'Semua']];
function render(r) {
  const s = r.summary, d = r.digital;
  $('#report').innerHTML = `
    <div class="title"><h2>${esc(r.industry)} · ${esc(r.area)}</h2><span>${esc(r.created.slice(0,16).replace('T',' '))} UTC</span></div>
    <div class="kpis">
      <div class="kpi"><div class="l">Bisnis ditemukan</div><div class="v">${s.businesses}</div><div class="s">batas bawah</div></div>
      <div class="kpi"><div class="l">Rating rata-rata</div><div class="v">★ ${s.avg_rating ?? '–'}</div><div class="s">median ${num(s.median_reviews)} review</div></div>
      <div class="kpi"><div class="l">Penguasaan 3 teratas</div><div class="v">${s.top3_share}%</div><div class="s">${esc(s.concentration)}</div></div>
      <div class="kpi"><div class="l">Bisa di-WhatsApp</div><div class="v">${d.whatsapp}%</div><div class="s">website ${d.website}% · IG ${d.instagram}% (n=${d.sampled})</div></div>
    </div>
    ${r.ai_strategy ? `<div class="card ai"><h3>Strategi <small>disusun AI dari data di bawah</small></h3><ul class="ins">${r.ai_strategy.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
    <div class="card"><h3>Temuan utama</h3><ul class="ins">${r.insights.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
    <div class="grid2" style="margin-top:12px">
      <div class="card"><h3>Sebaran rating</h3>${bars(r.rating_buckets)}</div>
      <div class="card"><h3>Ukuran pemain <small>jumlah review</small></h3>${bars(r.size_buckets)}</div>
    </div>
    <div class="card"><h3>Prospek bisnis kecil <small>bagus tapi lemah digital — siap dihubungi</small></h3>${table(r.prospects, 'prospects')}
      <div class="note">Hubungi dengan sopan dan relevan; satu pesan, jangan spam. Nomor diambil dari profil Google Maps publik.</div></div>
    <div class="card"><h3>Peta pemain</h3>
      <div class="tabs">${TABS.map(([k, l], i) => `<button class="tab ${i ? '' : 'on'}" data-k="${k}">${l} (${(r[k] || []).length})</button>`).join('')}</div>
      <div id="tbl">${table(r.leaders)}</div></div>`;
  document.querySelectorAll('.tab').forEach(b => b.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('on')); b.classList.add('on');
    $('#tbl').innerHTML = table(r[b.dataset.k]);
  });
}
$('#f').onsubmit = async e => {
  e.preventDefault();
  const r = await fetch('/market/run', {method: 'POST', body: JSON.stringify({industry: $('#industry').value, area: $('#area').value})});
  const j = await r.json();
  if (!r.ok) { $('#prog').textContent = j.error; return; }
  poll();
};
async function poll() {
  const j = await (await fetch('/log')).json();
  const lines = (j.log || '').trim().split('\\n').filter(l => /\\[\\d\\/5\\]|Tidak ada|Error|error/.test(l));
  $('#go').disabled = j.running;
  if (j.running) { $('#prog').textContent = '⏳ ' + (lines.pop() || 'Memulai…').replace(/^\\S+ /, ''); setTimeout(poll, 1500); }
  else if (wasRunning) {
    const m = (j.log || '').match(/REPORT_ID=(\\d+)/);
    $('#prog').textContent = m ? '✓ Selesai' : '✗ ' + ((j.log || '').trim().split('\\n').slice(-2).join(' ') || 'Gagal');
    current = null; loadRecent(m && m[1]);
  }
  wasRunning = j.running;
}
loadRecent(); poll();
</script>
</body>
</html>"""

NO_KEY_PAGE ="""<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8"><title>Riset Kompetitor</title>
<style>body{font-family:-apple-system,sans-serif;max-width:560px;margin:15vh auto;padding:0 16px;color:#1e293b;line-height:1.6}
code{background:#f1f5f9;padding:2px 6px;border-radius:4px}</style></head><body>
<h2>Chat butuh API key OpenRouter</h2>
<p>Isi <code>OPENROUTER_API_KEY</code> di file <code>.env</code>, lalu jalankan ulang:</p>
<p><code>source .env &amp;&amp; python app.py</code></p>
<p>Sementara itu pakai <a href="/market">Riset pasar</a> (tidak butuh key) atau <a href="/tools">mode manual</a>.</p></body></html>"""


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
            self._send(200, CHAT_PAGE if OPENROUTER_KEY else NO_KEY_PAGE)
        elif path == "/market":
            self._send(200, MARKET_PAGE)
        elif path == "/market/reports":
            rows = []
            if os.path.exists(os.path.join(HERE, "ig.db")):
                conn = sqlite3.connect(os.path.join(HERE, "ig.db"))
                try:
                    rows = [{"id": i, "industry": a, "area": b, "created": c} for i, a, b, c in conn.execute(
                        "SELECT id, industry, area, created FROM market_reports ORDER BY id DESC LIMIT 12")]
                except sqlite3.OperationalError:
                    pass
                finally:
                    conn.close()
            self._send(200, json.dumps(rows, ensure_ascii=False), "application/json")
        elif path == "/market/report":
            rid = re.sub(r"\D", "", (self.path.split("id=", 1) + [""])[1].split("&")[0])
            conn = sqlite3.connect(os.path.join(HERE, "ig.db"))
            try:
                row = conn.execute("SELECT report FROM market_reports WHERE id=?", (rid or 0,)).fetchone()
            finally:
                conn.close()
            self._send(200 if row else 404, row[0] if row else '{"error":"tidak ada"}', "application/json")
        elif path == "/chat/poll":
            jid = (self.path.split("job=", 1) + [""])[1].split("&")[0]
            with CHAT["lock"]:
                job = CHAT["jobs"].get(jid)
                body = dict(job, steps=list(job["steps"])) if job else {"done": True, "error": "job tidak ada", "steps": [], "answer": ""}
            self._send(200, json.dumps(body, ensure_ascii=False), "application/json")
        elif path == "/tools":
            opts = "".join(f'<option value="{c}">{c}</option>' for c in CATEGORIES)
            ph = ("tanya agent: kompetitor cafe di Kemang yang paling berkembang siapa?"
                  if OPENROUTER_KEY else "cariin cafe di Kemang, Jakarta Selatan radius 2 km")
            self._send(200, PAGE.replace("CATEGORY_OPTIONS", opts).replace("PROMPT_PLACEHOLDER", ph))
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
        if self.path not in ("/run", "/ask", "/chat", "/market/run"):
            return self._send(404, "Not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, json.dumps({"error": "Request tidak valid."}), "application/json")
        if self.path == "/market/run":
            industry = re.sub(r"[^\w\s&.,/-]", "", str(data.get("industry", ""))).strip()[:60]
            area = re.sub(r"[^\w\s&.,/-]", "", str(data.get("area", ""))).strip()[:80]
            if not industry or not area:
                return self._send(400, json.dumps({"error": "Isi industri dan wilayah."}), "application/json")
            err = launch([sys.executable, "-W", "ignore", "-u", "market.py",
                          "--industry", industry, "--area", area])
            if err:
                return self._send(409, json.dumps({"error": err}), "application/json")
            return self._send(200, json.dumps({"ok": True}), "application/json")
        if self.path == "/chat":
            msg = str(data.get("message", "")).strip()[:2000]
            sid = re.sub(r"[^a-z0-9]", "", str(data.get("sid", "")))[:20] or "default"
            if not msg:
                return self._send(400, json.dumps({"error": "Pesan kosong."}), "application/json")
            if not OPENROUTER_KEY:
                return self._send(400, json.dumps({"error": "OPENROUTER_API_KEY belum diisi."}), "application/json")
            jid, err = chat_start(sid, msg)
            if err:
                return self._send(409, json.dumps({"error": err}), "application/json")
            return self._send(200, json.dumps({"job": jid}), "application/json")
        if self.path == "/ask":
            prompt = str(data.get("prompt", ""))[:300]
            if OPENROUTER_KEY:  # mode agent: LLM yang memutuskan tool apa yang dipanggil
                err = launch([sys.executable, "-W", "ignore", "-u", "agent.py", prompt])
                if err:
                    return self._send(400, json.dumps({"error": err}), "application/json")
                return self._send(200, json.dumps({"ok": True, "agent": True}), "application/json")
            parsed = parse_prompt(prompt)
            if "category" not in parsed:
                return self._send(400, json.dumps({"error": "Jenis bisnisnya apa? Contoh: cafe, resto, salon, baju, gym, laundry."}), "application/json")
            if not parsed["location"]:
                return self._send(400, json.dumps({"error": "Lokasinya di mana? Contoh: \"cafe di Kemang, Jakarta\"."}), "application/json")
            parsed["radius"] = min(max(parsed["radius"], 200), 10000)
            parsed["ig_scrape"] = min(max(parsed["ig_scrape"], 0), 15)
            data = {"job": "pipeline", **parsed}
            err = start_job(data)
            if err:
                return self._send(400, json.dumps({"error": err}), "application/json")
            return self._send(200, json.dumps({"ok": True, "parsed": parsed}), "application/json")
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
