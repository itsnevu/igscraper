"""
agent.py — agent riset kompetitor lokal (OpenRouter + tool calling).

Agent memutuskan sendiri tool mana yang dipanggil:
  - search_competitors : jalankan pipeline (OSM -> website -> IG -> Social Blade -> Trends)
  - list_competitors   : baca hasil yang sudah tersimpan di ig.db
  - scrape_instagram   : scrape detail akun IG tertentu
  - google_trends      : bandingkan minat pencarian Google untuk beberapa nama

Setup:
  export OPENROUTER_API_KEY="sk-or-..."      # openrouter.ai/keys
  export OPENROUTER_MODEL="..."              # opsional; harus mendukung tool calling

Usage:
  python agent.py                              # mode chat interaktif
  python agent.py "kompetitor cafe di Kemang yang paling berkembang siapa?"
"""
import json
import os
import sqlite3
import subprocess
import sys

import netutil
from pipeline import CATEGORIES

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "ig.db")
API_KEY = os.getenv("OPENROUTER_API_KEY", "")
# kosong = pilih otomatis model gratis (":free") yang mendukung tool calling
MODEL = os.getenv("OPENROUTER_MODEL", "")
# urutan coba: murah tapi pintar (<Rp10/pertanyaan) -> gratis (kalau saldo habis / error 402)
PREFERRED = ["deepseek/deepseek-v4.1-flash", "qwen/qwen3.7-flash",
             "google/gemma-4-31b-it:free", "nvidia/nemotron-3-super-120b-a12b:free",
             "nvidia/nemotron-3-ultra-550b-a55b:free", "thinkingmachines/inkling:free"]
MAX_STEPS = 8

SYSTEM = """Kamu analis riset kompetitor untuk pemilik bisnis lokal di Indonesia.
Jawab dalam bahasa Indonesia santai, ringkas, dan berbasis data dari tool.
- SELALU panggil list_competitors dulu. Hanya panggil search_competitors kalau hasilnya kosong
  untuk lokasi+kategori itu, atau user eksplisit minta data terbaru. Jangan scrape ulang data yang sudah ada.
- Jangan panggil scrape_instagram untuk akun yang datanya sudah ada di list_competitors,
  kecuali user butuh detail (frekuensi posting, jam terbaik) yang belum ada.
- "Berkembang" = followers besar, followers_delta positif, ER tinggi, tren Google naik.
- Akun IG bertanda instagram_source=bing adalah tebakan pencarian; sebutkan kalau relevan.
- Jangan mengarang angka. Kalau data kosong, bilang terus terang dan sarankan langkah berikutnya."""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_competitors",
        "description": "Cari bisnis kategori tertentu di sekitar lokasi, lalu kumpulkan IG, followers, ER, "
                       "tren Google. Butuh beberapa menit. Hasil tersimpan di database.",
        "parameters": {"type": "object", "properties": {
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "location": {"type": "string", "description": "mis. 'Kemang, Jakarta Selatan'"},
            "radius": {"type": "integer", "description": "meter, default 2000"},
            "ig_scrape": {"type": "integer", "description": "jumlah akun IG teratas yang di-scrape detail, default 3"},
        }, "required": ["category", "location"]}}},
    {"type": "function", "function": {
        "name": "list_competitors",
        "description": "Ambil kompetitor yang sudah tersimpan, urut followers. Filter opsional.",
        "parameters": {"type": "object", "properties": {
            "location": {"type": "string", "description": "potongan nama lokasi, mis. 'Kemang'"},
            "category": {"type": "string"},
            "only_with_instagram": {"type": "boolean"},
            "limit": {"type": "integer", "description": "default 20"},
        }}}},
    {"type": "function", "function": {
        "name": "scrape_instagram",
        "description": "Scrape detail akun IG (followers, ER, rata-rata likes/komentar, frekuensi posting, "
                       "hari & jam terbaik). Maks 5 akun per panggilan.",
        "parameters": {"type": "object", "properties": {
            "usernames": {"type": "array", "items": {"type": "string"}},
        }, "required": ["usernames"]}}},
    {"type": "function", "function": {
        "name": "google_trends",
        "description": "Bandingkan minat pencarian Google (Indonesia) untuk maks 5 nama brand, 3 bulan terakhir.",
        "parameters": {"type": "object", "properties": {
            "keywords": {"type": "array", "items": {"type": "string"}},
        }, "required": ["keywords"]}}},
]


# ------------------------------------------------------------
# tools
# ------------------------------------------------------------

def _run(cmd):
    p = subprocess.run([sys.executable, "-W", "ignore"] + cmd, cwd=HERE,
                       capture_output=True, text=True)
    return (p.stdout + p.stderr)[-1500:]


def tool_search_competitors(category, location, radius=2000, ig_scrape=3):
    if category not in CATEGORIES:
        return {"error": f"kategori harus salah satu dari {list(CATEGORIES)}"}
    radius = min(max(int(radius or 2000), 200), 10000)
    ig_scrape = min(max(int(ig_scrape or 3), 0), 15)
    # guard: kalau lokasi+kategori ini sudah dipindai < 24 jam, pakai data yang ada
    cached = tool_list_competitors(location=location.split(",")[0], category=category, limit=25)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()  # last_scanned disimpan dalam UTC
    if cached["count"] and any(i["scanned"] == today for i in cached["items"]):
        return {"note": "sudah dipindai hari ini, pakai data tersimpan (tidak scrape ulang)",
                "data": cached}
    log = _run(["pipeline.py", "--location", location, "--category", category,
                "--radius", str(radius), "--ig-scrape", str(ig_scrape)])
    return {"log_tail": log[-600:], "data": tool_list_competitors(location=location.split(",")[0],
                                                                    category=category, limit=25)}


def tool_list_competitors(location=None, category=None, only_with_instagram=False, limit=20):
    if not os.path.exists(DB):
        return {"count": 0, "items": [], "note": "database kosong, panggil search_competitors"}
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute("SELECT location, data, last_scanned FROM businesses").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    items = []
    for loc, data, scanned in rows:
        b = json.loads(data)
        if location and location.lower() not in (loc or "").lower():
            continue
        if category and b.get("category") != category:
            continue
        if only_with_instagram and not b.get("instagram"):
            continue
        items.append({k: b.get(k) for k in (
            "name", "address", "instagram", "instagram_source", "followers", "followers_delta",
            "engagement_rate", "avg_likes", "grade", "website", "phone", "search_trend")}
            | {"location": loc, "scanned": (scanned or "")[:10]})
    items.sort(key=lambda x: -(x.get("followers") or 0))
    return {"count": len(items), "items": items[:int(limit or 20)]}


def tool_scrape_instagram(usernames):
    users = [u.strip().lstrip("@") for u in usernames][:5]
    if not users:
        return {"error": "usernames kosong"}
    # ig_scraper.py sendiri melewati akun yang di-scrape < 24 jam (diff mode)
    _run(["ig_scraper.py", "--mode", "direct", "--targets", ",".join(users), "--out", "hasil"])
    conn = sqlite3.connect(DB)
    out = []
    try:
        for u in users:
            row = conn.execute("SELECT data FROM profiles WHERE lower(username)=?", (u.lower(),)).fetchone()
            if not row:
                out.append({"username": u, "error": "gagal diambil"})
                continue
            d = json.loads(row[0])
            s = d.get("stats") or {}
            out.append({"username": u, "followers": d.get("followers"), "posts": d.get("posts_count"),
                        "bio": d.get("biography"), "engagement_rate": s.get("engagement_rate"),
                        "avg_likes": s.get("avg_likes"), "avg_comments": s.get("avg_comments"),
                        "posts_per_week": s.get("posts_per_week"), "media_mix": s.get("media_mix"),
                        "best_day": s.get("best_day"), "best_hour_utc": s.get("best_hour_utc"),
                        "growth": s.get("growth")})
    finally:
        conn.close()
    return {"profiles": out}


def tool_google_trends(keywords):
    from google_trends import trends
    try:
        df = trends(keywords[:5]).drop(columns=["isPartial"], errors="ignore")
    except Exception as e:
        return {"error": str(e)}
    if df.empty:
        return {"note": "volume pencarian terlalu kecil"}
    return {c: {"avg_4w": round(float(df[c].tail(28).mean()), 1),
                "avg_prev_4w": round(float(df[c].iloc[-56:-28].mean()), 1),
                "latest": int(df[c].iloc[-1])} for c in df.columns}


TOOL_FUNCS = {
    "search_competitors": tool_search_competitors,
    "list_competitors": tool_list_competitors,
    "scrape_instagram": tool_scrape_instagram,
    "google_trends": tool_google_trends,
}


# ------------------------------------------------------------
# agent loop
# ------------------------------------------------------------

_models = []


def candidate_models():
    """MODEL dari env dulu, lalu PREFERRED yang tersedia, lalu sisa model gratis + tools."""
    if _models:
        return _models
    if MODEL:
        _models.append(MODEL)
    try:
        data = netutil.get("https://openrouter.ai/api/v1/models").json()["data"]
        tools = {m["id"] for m in data if "tools" in (m.get("supported_parameters") or [])}
        _models.extend(m for m in PREFERRED if m in tools and m not in _models)
        _models.extend(sorted(m for m in tools if m.endswith(":free") and m not in _models))
    except Exception as e:
        print(f"[agent] gagal ambil daftar model: {e}", flush=True)
        _models.extend(m for m in PREFERRED if m not in _models)
    return _models


def chat(messages):
    last = None
    for model in list(candidate_models()):
        r = netutil.post("https://openrouter.ai/api/v1/chat/completions", retries=2, timeout=120,
                         headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json",
                                  "X-Title": "igscraper-agent"},
                         json={"model": model, "messages": messages, "tools": TOOLS, "temperature": 0.2})
        try:
            j = r.json()
        except Exception:
            j = {"error": {"message": f"HTTP {r.status_code}"}}
        if "error" not in j and j.get("choices"):
            if _models[0] != model:  # model ini jalan -> pakai terus untuk langkah berikutnya
                _models.remove(model)
                _models.insert(0, model)
                print(f"[agent] pakai model: {model}", flush=True)
            return j["choices"][0]["message"]
        last = (j.get("error") or {}).get("message", j)
        print(f"[agent] {model} gagal: {str(last)[:120]} — coba model lain", flush=True)
        _models.remove(model)
    raise RuntimeError(f"Semua model gratis gagal. Terakhir: {last}")


def run_agent(question, history=None, log=print):
    messages = history if history is not None else [{"role": "system", "content": SYSTEM}]
    messages.append({"role": "user", "content": question})
    for _ in range(MAX_STEPS):
        msg = chat(messages)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "", messages
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            log(f"🔧 {name}({json.dumps(args, ensure_ascii=False)})")
            try:
                result = TOOL_FUNCS[name](**args) if name in TOOL_FUNCS else {"error": "tool tidak dikenal"}
            except Exception as e:
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": json.dumps(result, ensure_ascii=False, default=str)[:12000]})
    return "Berhenti: terlalu banyak langkah. Coba pertanyaan yang lebih spesifik.", messages


def main():
    if not API_KEY:
        sys.exit("Set dulu: export OPENROUTER_API_KEY=\"sk-or-...\" (buat di openrouter.ai/keys)")
    print(f"Model: {MODEL or 'otomatis (murah -> gratis)'}", flush=True)
    if len(sys.argv) > 1:
        answer, _ = run_agent(" ".join(sys.argv[1:]), log=lambda s: print(s, flush=True))
        print("\n" + answer)
        return
    history = None
    print("Agent riset kompetitor. Ketik pertanyaan (Ctrl+C untuk keluar).")
    while True:
        try:
            q = input("\nKamu: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if q:
            answer, history = run_agent(q, history)
            print("\nAgent:", answer)


if __name__ == "__main__":
    main()
