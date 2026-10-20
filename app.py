"""
app.py, UI lokal sederhana untuk ig_scraper.py.

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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import html as htmllib
import sqlite3

from pipeline import CATEGORIES
from report_dashboard import CSS as REPORT_CSS, JS as REPORT_JS, build_html, fmt_num, load_profiles

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8000


def load_env(path=os.path.join(HERE, ".env")):
    """Isi os.environ dari .env (format `export KEY="nilai"`), tanpa menimpa yang sudah di-set."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s#]*))', line)
            if m and m.group(1) not in os.environ:
                val = next(g for g in m.groups()[1:] if g is not None)
                if val:
                    os.environ[m.group(1)] = val


load_env()

JOB = {"proc": None, "log": [], "lock": threading.Lock()}

PAGE = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pengaturan lanjutan · Raqio</title>
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
  <p style="margin:0 0 6px"><a href="/market" style="color:#475569;font-size:13px">← Kembali ke Riset</a></p>
  <h1>Pengaturan lanjutan</h1>

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
    <label for="targets">Atau scrape akun IG tertentu, username target (pisahkan dengan koma atau baris baru)</label>
    <textarea id="targets" placeholder="nike, adidas"></textarea>
    <div class="row">
      <div>
        <label for="mode">Koneksi</label>
        <select id="mode">
          <option value="direct">Aman, pelan (koneksi internet sendiri)</option>
          <option value="mobile">Hotspot HP (ganti IP otomatis)</option>
          <option value="webshare">Cepat, pakai proxy Webshare</option>
          <option value="custom">Cepat, pakai daftar proxy sendiri</option>
        </select>
      </div>
      <div>
        <label for="maxposts">Maks post per profil</label>
        <input id="maxposts" type="number" min="1" max="100" placeholder="ikut mode">
      </div>
    </div>
    <label class="check"><input type="checkbox" id="force"> Scrape ulang walau baru di-scrape (&lt; 24 jam)</label>
    <button class="runbtn" onclick="run()">Jalankan</button><span id="status"></span>
    <div class="hint">Mode aman: sekitar 15 akun per jam supaya tidak diblokir Instagram. Lebih dari itu, proses menunggu sendiri.</div>
  </div>

  <div class="panel">
    <label>Status: <span id="runstate">tidak ada proses</span></label>
    <details><summary style="cursor:pointer;font-size:13px;color:#475569">Lihat detail teknis</summary>
    <pre id="log" style="margin-top:8px">Belum ada proses.</pre></details>
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
  const last = (j.log || '').trim().split('\\n').filter(l => l.trim() && !l.startsWith('$')).pop() || '';
  document.getElementById('runstate').textContent = j.running ? 'sedang berjalan: ' + last.replace(/^\\d\\d:\\d\\d:\\d\\d\\s*/, '').slice(0, 120)
    : j.stopped ? 'dihentikan' : /exit code 0/.test(last) ? 'selesai' : j.log ? 'berhenti dengan error, buka detail teknis' : 'tidak ada proses';
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


def stop_job():
    with JOB["lock"]:
        proc = JOB["proc"]
        if not proc or proc.poll() is not None:
            return False
        JOB["stopped"] = True
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return True


def launch(cmd):
    with JOB["lock"]:
        if JOB["proc"] and JOB["proc"].poll() is None:
            return "Masih ada proses yang berjalan."
        JOB["log"] = ["$ " + " ".join(cmd[3:]) + "\n"]
        JOB["started"] = time.time()
        JOB["stopped"] = False
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
                   ) if ig else '<span style="color:#cbd5e1">-</span>'
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
<title>Kompetitor IG · Raqio</title><style>{REPORT_CSS}</style></head><body>
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


class ChatStopped(Exception):
    pass


def chat_stop(jid):
    with CHAT["lock"]:
        job = CHAT["jobs"].get(jid)
        if not job or job["done"]:
            return False
        job["cancel"] = True
    # agent mungkin sedang menunggu tool/LLM; tandai selesai sekarang supaya UI tidak menunggu
    job["error"], job["done"] = "Dihentikan.", True
    return True


def chat_start(sid: str, message: str):
    import agent
    with CHAT["lock"]:
        sess = CHAT["sessions"].setdefault(sid, {"history": None, "busy": False})
        if sess["busy"]:
            return None, "Agent masih menjawab pesan sebelumnya."
        sess["busy"] = True
        CHAT["seq"] += 1
        jid = str(CHAT["seq"])
        job = CHAT["jobs"][jid] = {"steps": [], "done": False, "answer": "", "error": "", "cancel": False}

    def step(s):
        if job["cancel"]:
            raise ChatStopped()
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
            if job["cancel"]:
                raise ChatStopped()
            job["answer"] = answer
        except ChatStopped:
            job["error"] = "Dihentikan."
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
<title>Asisten · Raqio</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #F6F5F3; --panel: #FFFFFF; --sunk: #ECEBE9; --line: #E2E1E0; --line-2: #EFEEEC;
  --text: #0B0B0B; --muted: #6F6E6B; --faint: #A3A29F;
  --ink: #161616; --ink-text: #F8F8F8; --accent: #00BC87; --accent-soft: #E3F6EF; --bad: #D4483B;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0B0B0B; --panel: #141414; --sunk: #1C1C1C; --line: #262626; --line-2: #1E1E1E;
  --text: #F2F2F0; --muted: #9A9996; --faint: #6A6967;
  --ink: #F2F2F0; --ink-text: #0B0B0B; --accent: #19D29C; --accent-soft: #0F2A21; --bad: #EF6B5F; } }
:root[data-theme="dark"] {
  --bg: #0B0B0B; --panel: #141414; --sunk: #1C1C1C; --line: #262626; --line-2: #1E1E1E;
  --text: #F2F2F0; --muted: #9A9996; --faint: #6A6967;
  --ink: #F2F2F0; --ink-text: #0B0B0B; --accent: #19D29C; --accent-soft: #0F2A21; --bad: #EF6B5F; }
* { box-sizing: border-box; }
html, body { margin: 0; }
body { font-family: "Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg);
       color: var(--text); font-size: 14px; -webkit-font-smoothing: antialiased; }
a { color: inherit; }
button { font: inherit; cursor: pointer; }
code { font-family: "Geist Mono", ui-monospace, monospace; font-size: 12.5px; background: var(--sunk); padding: 2px 6px; border-radius: 5px; }
.top { display: flex; align-items: center; gap: 20px; padding: 14px 28px; border-bottom: 1px solid var(--line); background: var(--bg);
       position: sticky; top: 0; z-index: 20; }
.brand { font-weight: 700; letter-spacing: -.02em; font-size: 15px; display: flex; align-items: center; gap: 8px; text-decoration: none; }
.brand i { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); display: inline-block; }
.top nav { margin-left: auto; display: flex; gap: 18px; flex-wrap: wrap; }
.top nav a { text-decoration: none; color: var(--muted); font-size: 13px; }
.top nav a:hover, .top nav a.on { color: var(--text); }
.hero { max-width: 820px; margin: 0 auto; padding: 9vh 0 0; }
h1 { font-size: 44px; letter-spacing: -.04em; line-height: 1.05; margin: 0 0 12px; font-weight: 600; }
h1 span { color: var(--faint); }
.lead { color: var(--muted); font-size: 16px; line-height: 1.55; margin: 0 0 26px; max-width: 600px; }
.btn { display: inline-flex; align-items: center; justify-content: center; background: var(--ink); color: var(--ink-text); border: 0;
       border-radius: 10px; padding: 0 20px; font-weight: 500; height: 42px; white-space: nowrap; text-decoration: none; }
.btn:disabled { opacity: .45; cursor: default; }
.btn.ghost { background: var(--panel); color: var(--text); border: 1px solid var(--line); }
.feats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 36px; }
.feat { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; text-decoration: none; display: block; }
.feat:hover { border-color: var(--faint); }
.feat b { display: block; font-weight: 600; margin-bottom: 4px; }
.feat span { color: var(--muted); font-size: 13px; line-height: 1.5; }
.feat em { font-style: normal; color: var(--accent); font-size: 12px; display: block; margin-top: 10px; }
@media (max-width: 720px) { .top { padding: 12px 16px; gap: 12px; } .top nav { gap: 12px; } h1 { font-size: 30px; }
  .hero { padding-top: 5vh; } .feats { grid-template-columns: 1fr; } }
</style>
<style>
html, body { height: 100%; }
body { display: flex; flex-direction: column; }
#msgs { flex: 1; overflow-y: auto; padding: 0 16px 24px; }
.wrap { max-width: 820px; margin: 0 auto; }
#list { padding-top: 24px; }
.msg { margin-bottom: 20px; line-height: 1.6; font-size: 15px; }
.msg.me { background: var(--ink); color: var(--ink-text); padding: 10px 14px; border-radius: 14px 14px 4px 14px; margin-left: auto;
          width: fit-content; max-width: 85%; white-space: pre-wrap; }
.msg.bot { overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px; }
.msg.bot p { margin: 0 0 10px; }
.msg.bot p:last-child { margin-bottom: 0; }
.msg.bot a { color: var(--accent); }
.msg.bot table { border-collapse: collapse; margin: 8px 0 12px; font-size: 13px; }
.msg.bot th, .msg.bot td { border-bottom: 1px solid var(--line-2); padding: 7px 10px; text-align: left; }
.msg.bot th { color: var(--muted); font-weight: 500; font-size: 12px; }
.msg.bot ul, .msg.bot ol { margin: 0 0 10px; padding-left: 22px; }
.msg.bot h1, .msg.bot h2, .msg.bot h3 { font-size: 15px; margin: 14px 0 6px; font-weight: 600; letter-spacing: 0; }
.steps { font-size: 12px; color: var(--muted); margin-bottom: 8px; font-family: "Geist Mono", ui-monospace, monospace; }
.steps div::before { content: "› "; color: var(--accent); }
.err { color: var(--bad); }
.typing::after { content: "▍"; animation: blink 1s steps(1) infinite; color: var(--accent); }
@keyframes blink { 50% { opacity: 0; } }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
.chip { border: 1px solid var(--line); background: transparent; color: var(--muted); border-radius: 999px;
        padding: 6px 12px; font-size: 13px; cursor: pointer; }
.chip:hover { color: var(--text); border-color: var(--faint); }
form { padding: 12px 16px 18px; background: var(--bg); }
form .wrap { display: flex; gap: 8px; background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 6px; }
form .wrap:focus-within { border-color: var(--faint); }
textarea { flex: 1; resize: none; height: 44px; max-height: 160px; padding: 11px 12px; font: inherit; font-size: 15px;
           border: 0; outline: none; background: transparent; color: var(--text); }
form .hint { max-width: 820px; margin: 8px auto 0; color: var(--faint); font-size: 12px; text-align: center; }
.embed .feats, .embed form .hint { display: none; }
.embed .hero { padding-top: 24px; }
.embed .hero h1 { font-size: 36px !important; }
.embed .lead { font-size: 15px !important; }
#send.stop { background: transparent !important; color: var(--text) !important; border: 1px solid var(--line) !important; }
</style>
</head>
<body>
<div class="top">
  <a class="brand" href="/"><i></i>raqio</a>
  <nav><a href="/" class="on">Chat</a><a href="/market">Riset pasar</a><a href="/competitors" target="_blank">Kompetitor IG</a>
    <a href="/report" target="_blank">Analisis IG</a><a href="/tools">Manual</a><a href="#" onclick="newChat();return false">Chat baru</a></nav>
</div>
<div id="msgs"><div class="wrap" id="list">
  <div class="hero" id="empty">
    <h1>Kenali pasarmu. <span>Temukan celahnya.</span></h1>
    <p class="lead">Tanya bebas soal bisnis apa pun di lokasi mana pun. Agent akan mencari pemainnya, mengecek IG, website,
      dan tren, lalu merangkum siapa yang kuat, siapa yang lemah, dan di mana peluangnya.</p>
    <div class="chips">
      <span class="chip">kompetitor coffee shop di Kemang siapa aja?</span>
      <span class="chip">riset pasar agency di Gading Serpong</span>
      <span class="chip">salon di Dago Bandung yang paling rame di IG</span>
      <span class="chip">bandingin Kopi Kenangan sama Janji Jiwa</span>
    </div>
    <div class="feats">
      <a class="feat" href="/market"><b>Riset pasar</b><span>Peta pemain per industri dan wilayah, skor kekuatan, dan prospek siap dihubungi.</span><em>Buka →</em></a>
      <a class="feat" href="/competitors" target="_blank"><b>Kompetitor IG</b><span>Followers, engagement, dan pertumbuhan akun Instagram pesaing.</span><em>Buka →</em></a>
      <a class="feat" href="/tools"><b>Mode manual</b><span>Atur sendiri kategori, radius, dan jumlah akun yang di-scrape.</span><em>Buka →</em></a>
    </div>
  </div>
</div></div>
<form id="form"><div class="wrap">
  <textarea id="input" placeholder="Tanya soal pasar atau kompetitor…" autofocus></textarea>
  <button class="btn" id="send" type="submit">Kirim</button>
</div><div class="hint">Enter untuk kirim, Shift+Enter untuk baris baru</div></form>
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
  add('me', esc(text)); input.value = '';
  const bot = add('bot', '<div class="steps"></div><div class="typing"></div>');
  const r = await fetch('/chat', {method: 'POST', body: JSON.stringify({sid, message: text})});
  const j = await r.json();
  if (!r.ok) { bot.innerHTML = '<span class="err">' + esc(j.error) + '</span>'; return; }
  busy(j.job);
  const poll = async () => {
    const p = await (await fetch('/chat/poll?job=' + j.job)).json();
    bot.querySelector('.steps').innerHTML = p.steps.map(s => '<div>' + esc(stepText(s)) + '</div>').join('');
    if (p.done) {
      bot.innerHTML = (p.steps.length ? '<div class="steps">' + p.steps.map(s => '<div>' + esc(stepText(s)) + '</div>').join('') + '</div>' : '')
        + (p.error ? '<span class="err">' + esc(p.error) + '</span>' : md(p.answer) + actions(p.answer));
      busy(null); input.focus();
    } else setTimeout(poll, 1200);
    box.scrollTop = box.scrollHeight;
  };
  poll();
}
let running = null;
function busy(jid) {
  running = jid;
  send.textContent = jid ? 'Stop' : 'Kirim';
  send.classList.toggle('stop', !!jid);
}
// tombol lanjutan di bawah jawaban, supaya jawaban bisa langsung ditindaklanjuti
function actions(answer) {
  const a = [];
  if (/riset|pasar|wilayah|prospek/i.test(answer)) a.push(['/market', 'Buka laporan riset']);
  if (/prospek|whatsapp|hubungi/i.test(answer)) a.push(['/prospects', 'Lihat prospek']);
  if (/instagram|followers|@\\w/i.test(answer)) a.push(['/competitors', 'Data kompetitor IG']);
  if (!a.length) return '';
  return '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">' +
    a.map(([h, l]) => `<a class="btn ghost" style="height:34px;font-size:11px!important" href="${h}" target="_top">${l}</a>`).join('') + '</div>';
}
function newChat() {
  sid = Math.random().toString(36).slice(2);
  try { localStorage.setItem('sid', sid); } catch (e) {}
  location.reload();
}
document.getElementById('form').onsubmit = async e => {
  e.preventDefault();
  if (running) { await fetch('/chat/stop', {method: 'POST', body: JSON.stringify({job: running})}); return; }
  ask(input.value);
};
input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!running) ask(input.value); } });
document.querySelectorAll('.chip').forEach(c => c.onclick = () => ask(c.textContent));
</script>
</body>
</html>"""

def themed(html):
    """Tempel templates/theme.css (dibaca tiap request, edit langsung terlihat)."""
    with open(os.path.join(HERE, "templates", "theme.css"), encoding="utf-8") as f:
        css = f.read()
    font = ('<link href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;700'
            '&display=swap" rel="stylesheet">')
    shell = '<link rel="icon" href="/static/logo.svg" type="image/svg+xml"><link rel="stylesheet" href="/static/shell.css"><script src="/static/shell.js" defer></script>'
    return html.replace("</head>", f"{font}{shell}<style>{css}</style></head>", 1)


def template(name):
    with open(os.path.join(HERE, "templates", name), encoding="utf-8") as f:
        return f.read()


def ig_profile(username):
    """Ringkasan profil IG yang pernah di-scrape (untuk panel detail bisnis)."""
    if not username or not os.path.exists(os.path.join(HERE, "ig.db")):
        return {"found": False}
    conn = sqlite3.connect(os.path.join(HERE, "ig.db"))
    try:
        row = conn.execute("SELECT data, followers_history, last_scraped FROM profiles WHERE lower(username)=?",
                           (username,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"found": False, "username": username}
    d, hist = json.loads(row[0] or "{}"), json.loads(row[1] or "[]")
    st = d.get("stats") or {}
    delta = None
    if len(hist) > 1:
        delta = (hist[-1].get("followers") or 0) - (hist[0].get("followers") or 0)
    return {"found": True, "username": d.get("username") or username, "full_name": d.get("full_name"),
            "biography": d.get("biography"), "followers": d.get("followers"), "following": d.get("following"),
            "posts_count": d.get("posts_count"), "is_verified": d.get("is_verified"),
            "engagement_rate": st.get("engagement_rate"), "posts_per_week": st.get("posts_per_week"),
            "avg_likes": st.get("avg_likes"), "best_day": st.get("best_day"),
            "top_posts": (st.get("top_posts") or [])[:3], "followers_delta": delta,
            "tracked_since": hist[0]["ts"] if hist else None, "last_scraped": row[2]}


def market_page():
    # template dibaca tiap request, jadi edit HTML langsung terlihat tanpa restart
    with open(os.path.join(HERE, "templates", "market.html"), encoding="utf-8") as f:
        return f.read()

NO_KEY_PAGE = """<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Raqio</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #F6F5F3; --panel: #FFFFFF; --sunk: #ECEBE9; --line: #E2E1E0; --line-2: #EFEEEC;
  --text: #0B0B0B; --muted: #6F6E6B; --faint: #A3A29F;
  --ink: #161616; --ink-text: #F8F8F8; --accent: #00BC87; --accent-soft: #E3F6EF; --bad: #D4483B;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0B0B0B; --panel: #141414; --sunk: #1C1C1C; --line: #262626; --line-2: #1E1E1E;
  --text: #F2F2F0; --muted: #9A9996; --faint: #6A6967;
  --ink: #F2F2F0; --ink-text: #0B0B0B; --accent: #19D29C; --accent-soft: #0F2A21; --bad: #EF6B5F; } }
:root[data-theme="dark"] {
  --bg: #0B0B0B; --panel: #141414; --sunk: #1C1C1C; --line: #262626; --line-2: #1E1E1E;
  --text: #F2F2F0; --muted: #9A9996; --faint: #6A6967;
  --ink: #F2F2F0; --ink-text: #0B0B0B; --accent: #19D29C; --accent-soft: #0F2A21; --bad: #EF6B5F; }
* { box-sizing: border-box; }
html, body { margin: 0; }
body { font-family: "Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg);
       color: var(--text); font-size: 14px; -webkit-font-smoothing: antialiased; }
a { color: inherit; }
button { font: inherit; cursor: pointer; }
code { font-family: "Geist Mono", ui-monospace, monospace; font-size: 12.5px; background: var(--sunk); padding: 2px 6px; border-radius: 5px; }
.top { display: flex; align-items: center; gap: 20px; padding: 14px 28px; border-bottom: 1px solid var(--line); background: var(--bg);
       position: sticky; top: 0; z-index: 20; }
.brand { font-weight: 700; letter-spacing: -.02em; font-size: 15px; display: flex; align-items: center; gap: 8px; text-decoration: none; }
.brand i { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); display: inline-block; }
.top nav { margin-left: auto; display: flex; gap: 18px; flex-wrap: wrap; }
.top nav a { text-decoration: none; color: var(--muted); font-size: 13px; }
.top nav a:hover, .top nav a.on { color: var(--text); }
.hero { max-width: 820px; margin: 0 auto; padding: 9vh 0 0; }
h1 { font-size: 44px; letter-spacing: -.04em; line-height: 1.05; margin: 0 0 12px; font-weight: 600; }
h1 span { color: var(--faint); }
.lead { color: var(--muted); font-size: 16px; line-height: 1.55; margin: 0 0 26px; max-width: 600px; }
.btn { display: inline-flex; align-items: center; justify-content: center; background: var(--ink); color: var(--ink-text); border: 0;
       border-radius: 10px; padding: 0 20px; font-weight: 500; height: 42px; white-space: nowrap; text-decoration: none; }
.btn:disabled { opacity: .45; cursor: default; }
.btn.ghost { background: var(--panel); color: var(--text); border: 1px solid var(--line); }
.feats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 36px; }
.feat { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; text-decoration: none; display: block; }
.feat:hover { border-color: var(--faint); }
.feat b { display: block; font-weight: 600; margin-bottom: 4px; }
.feat span { color: var(--muted); font-size: 13px; line-height: 1.5; }
.feat em { font-style: normal; color: var(--accent); font-size: 12px; display: block; margin-top: 10px; }
@media (max-width: 720px) { .top { padding: 12px 16px; gap: 12px; } .top nav { gap: 12px; } h1 { font-size: 30px; }
  .hero { padding-top: 5vh; } .feats { grid-template-columns: 1fr; } }
</style>
<style>.wrap { padding: 0 16px 80px; } .acts { display: flex; gap: 8px; flex-wrap: wrap; }
.note { margin-top: 28px; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; color: var(--muted); line-height: 1.7; }
.note b { color: var(--text); font-weight: 600; }</style></head><body>
<div class="top"><a class="brand" href="/"><i></i>raqio</a>
  <nav><a href="/" class="on">Chat</a><a href="/market">Riset pasar</a><a href="/competitors" target="_blank">Kompetitor IG</a><a href="/tools">Manual</a></nav></div>
<div class="wrap"><div class="hero">
  <h1>Kenali pasarmu. <span>Temukan celahnya.</span></h1>
  <p class="lead">Peta pemain per industri dan wilayah, skor kekuatan, hidden gem, dan daftar prospek yang siap dihubungi lewat WhatsApp.</p>
  <div class="acts"><a class="btn" href="/market">Mulai riset pasar</a><a class="btn ghost" href="/tools">Mode manual</a></div>
  <div class="note"><b>Chat dengan agent belum aktif.</b> Isi <code>OPENROUTER_API_KEY</code> di file <code>.env</code>,
    lalu jalankan ulang <code>.venv/bin/python app.py</code>. Riset pasar dan mode manual tetap bisa dipakai tanpa key.</div>
  <div class="feats">
    <a class="feat" href="/market"><b>Riset pasar</b><span>Siapa yang kuat, siapa yang lemah, dan seberapa padat pasarnya.</span><em>Buka →</em></a>
    <a class="feat" href="/competitors" target="_blank"><b>Kompetitor IG</b><span>Followers, engagement, dan pertumbuhan akun pesaing.</span><em>Buka →</em></a>
    <a class="feat" href="/tools"><b>Mode manual</b><span>Atur sendiri kategori, radius, dan jumlah akun.</span><em>Buka →</em></a>
  </div>
</div></div></body></html>"""


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
            self._send(200, themed(CHAT_PAGE if OPENROUTER_KEY else NO_KEY_PAGE))
        elif path == "/market":
            self._send(200, themed(market_page()))
        elif path in ("/prospects", "/watch"):
            self._send(200, themed(template(path.strip("/") + ".html")))
        elif path.startswith("/static/"):
            name = os.path.basename(path)
            fp = os.path.join(HERE, "templates", name)
            if not re.fullmatch(r"[\w-]+\.(js|css|svg)", name) or not os.path.exists(fp):
                return self._send(404, "Not found")
            ctype = {"js": "text/javascript", "css": "text/css", "svg": "image/svg+xml"}[name.rsplit(".", 1)[1]]
            with open(fp, encoding="utf-8") as f:
                self._send(200, f.read(), ctype + "; charset=utf-8")
        elif path == "/prospects/data":
            import market
            self._send(200, json.dumps({"items": market.prospect_board(), "statuses": market.LEAD_STATUSES},
                                       ensure_ascii=False), "application/json")
        elif path == "/watch/data":
            import market
            self._send(200, json.dumps(market.watch_overview(), ensure_ascii=False), "application/json")
        elif path == "/business":
            from urllib.parse import parse_qs, urlparse
            ig = re.sub(r"[^A-Za-z0-9._]", "", parse_qs(urlparse(self.path).query).get("ig", [""])[0]).lower()
            self._send(200, json.dumps(ig_profile(ig), ensure_ascii=False), "application/json")
        elif path == "/market/watches":
            import market
            conn = market.db()
            try:
                rows = [{"industry": a, "area": b, "every_days": c, "last_run": d} for a, b, c, d in
                        conn.execute("SELECT industry, area, every_days, last_run FROM market_watches")]
            finally:
                conn.close()
            self._send(200, json.dumps(rows, ensure_ascii=False), "application/json")
        elif path == "/market/compare":
            import market
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            areas = [a for a in (q.get("areas", [""])[0]).split(";") if a.strip()]
            res = market.compare(q.get("industry", [""])[0], areas)
            self._send(200, json.dumps(res, ensure_ascii=False), "application/json")
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
            if not row:
                return self._send(404, '{"error":"tidak ada"}', "application/json")
            import market
            rep = market.score_all(json.loads(row[0]))  # laporan lama belum punya lead_score
            leads = market.get_leads()
            for p in rep.get("all") or []:
                if market.lead_key(p) in leads:
                    p["lead"] = leads[market.lead_key(p)]
            self._send(200, json.dumps(rep, ensure_ascii=False), "application/json")
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
            self._send(200, json.dumps({"running": running, "log": log, "started": JOB.get("started"),
                                        "stopped": JOB.get("stopped", False)}), "application/json")
        else:
            self._send(404, "Not found")

    def do_POST(self):
        if self.path not in ("/run", "/ask", "/chat", "/market/run", "/market/watch", "/market/compare/run",
                             "/market/lead", "/market/detail", "/stop", "/chat/stop"):
            return self._send(404, "Not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, json.dumps({"error": "Request tidak valid."}), "application/json")
        if self.path == "/market/lead":
            import market
            try:
                market.set_lead(str(data.get("key", ""))[:300], str(data.get("name", ""))[:200],
                                str(data.get("status", "")),
                                None if data.get("note") is None else str(data["note"])[:500],
                                None if data.get("remind") is None else str(data["remind"])[:10])
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}), "application/json")
            return self._send(200, json.dumps({"ok": True}), "application/json")
        if self.path == "/stop":
            return self._send(200, json.dumps({"ok": stop_job()}), "application/json")
        if self.path == "/chat/stop":
            return self._send(200, json.dumps({"ok": chat_stop(str(data.get("job", "")))}), "application/json")
        if self.path == "/market/detail":
            import market
            with JOB["lock"]:
                busy = bool(JOB["proc"] and JOB["proc"].poll() is None)
            if busy:  # dua browser Google Maps sekaligus memancing blokir
                return self._send(409, json.dumps({"error": "Tunggu riset yang sedang jalan selesai dulu."}),
                                  "application/json")
            try:
                p = market.detail_one(int(data.get("report_id") or 0), str(data.get("key", ""))[:300])
            except (ValueError, RuntimeError) as e:
                return self._send(400, json.dumps({"error": str(e)}), "application/json")
            return self._send(200, json.dumps(p, ensure_ascii=False), "application/json")
        if self.path == "/market/watch":
            import market
            ind = str(data.get("industry", "")).strip()[:60]
            area = str(data.get("area", "")).strip()[:80]
            conn = market.db()
            try:
                if data.get("on"):
                    conn.execute("INSERT OR IGNORE INTO market_watches (industry, area, every_days, last_run) "
                                 "VALUES (?,?,?,?)", (ind, area, int(data.get("every_days") or 7),
                                                      data.get("last_run")))
                else:
                    conn.execute("DELETE FROM market_watches WHERE lower(industry)=? AND lower(area)=?",
                                 (ind.lower(), area.lower()))
                conn.commit()
            finally:
                conn.close()
            return self._send(200, json.dumps({"ok": True}), "application/json")
        if self.path == "/market/compare/run":
            industry = re.sub(r"[^\w\s&.,/-]", "", str(data.get("industry", ""))).strip()[:60]
            areas = [re.sub(r"[^\w\s&.,/-]", "", str(a)).strip()[:80] for a in (data.get("areas") or [])]
            areas = [a for a in areas if a][:6]
            if not industry or len(areas) < 2:
                return self._send(400, json.dumps({"error": "Isi industri dan minimal 2 wilayah."}), "application/json")
            err = launch([sys.executable, "-W", "ignore", "-u", "market.py", "--industry", industry,
                          "--areas", ";".join(areas)])
            if err:
                return self._send(409, json.dumps({"error": err}), "application/json")
            return self._send(200, json.dumps({"ok": True}), "application/json")
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


def watch_loop():
    """Riset ulang otomatis wilayah yang dipantau (jalan selama app.py hidup)."""
    import time as _t
    from datetime import datetime, timezone
    import market
    while True:
        _t.sleep(60)
        try:
            with JOB["lock"]:
                busy = bool(JOB["proc"] and JOB["proc"].poll() is None)
            if busy:
                continue
            conn = market.db()
            try:
                rows = conn.execute("SELECT industry, area, every_days, last_run FROM market_watches").fetchall()
            finally:
                conn.close()
            now = datetime.now(timezone.utc)
            for ind, area, every, last in rows:
                due = not last or (now - datetime.fromisoformat(last)).total_seconds() > (every or 7) * 86400
                if due:
                    print(f"[pantau] riset ulang otomatis: {ind} · {area}", flush=True)
                    launch([sys.executable, "-W", "ignore", "-u", "market.py", "--industry", ind, "--area", area])
                    break  # satu per putaran, supaya tidak membebani Google Maps
        except Exception as e:
            print(f"[pantau] error: {e}", flush=True)
        _t.sleep(14 * 60)


if __name__ == "__main__":
    threading.Thread(target=watch_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"UI jalan di http://localhost:{PORT}  (Ctrl+C untuk berhenti)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
