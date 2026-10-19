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

from report_dashboard import build_html, load_profiles

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
</style>
</head>
<body>
<div class="wrap">
  <h1>IG Scraper</h1>

  <div class="panel">
    <label for="targets">Username target (pisahkan dengan koma atau baris baru)</label>
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
    <button id="run" onclick="run()">Jalankan</button><span id="status"></span>
    <div class="hint">Mode direct: ±15 profil per jam. Lebih dari itu, script menunggu sendiri.</div>
  </div>

  <div class="panel">
    <label>Log</label>
    <pre id="log">Belum ada proses.</pre>
  </div>

  <iframe id="report" src="/report"></iframe>
</div>
<script>
let wasRunning = false;
async function run() {
  const body = {
    targets: document.getElementById('targets').value,
    mode: document.getElementById('mode').value,
    max_posts: document.getElementById('maxposts').value,
    force: document.getElementById('force').checked,
  };
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
  document.getElementById('run').disabled = j.running;
  document.getElementById('status').textContent = j.running ? 'Sedang berjalan…' : '';
  if (wasRunning && !j.running) document.getElementById('report').src = '/report?' + Date.now();
  wasRunning = j.running;
  if (j.running) setTimeout(poll, 1500);
}
poll();
</script>
</body>
</html>"""


def start_job(data: dict):
    targets = [t.strip().lstrip("@") for t in re.split(r"[,\s]+", data.get("targets", "")) if t.strip()]
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
            self._send(200, PAGE)
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
