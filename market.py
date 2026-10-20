"""
market.py, engine riset pasar per industri x wilayah.

Sumber: Google Maps (tanpa login) + website bisnis (cari IG) + riwayat scan sebelumnya.

Usage:
  python market.py --industry "coffee shop" --area "Kemang Jakarta"
  python market.py --industry "salon" --area "Dago Bandung" --max 120 --details 40
Output: tabel market_reports & places di ig.db (dibaca UI /market)
"""
import argparse
import json
import logging
import math
import os
import re
import sqlite3
import statistics
import time
from collections import Counter
from datetime import datetime, timezone

import datastore
import gmaps

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("market")

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "ig.db")
IG_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{3,30})")
LINK_PAGES = ("linktr.ee", "heylink.me", "linkin.bio", "bio.link", "lynk.id", "msha.ke", "beacons.ai", "s.id")


def db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS places (
        place_id TEXT PRIMARY KEY, data TEXT, history TEXT, last_seen TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS market_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, industry TEXT, area TEXT, created TEXT, report TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS market_watches (
        industry TEXT, area TEXT, every_days INTEGER DEFAULT 7, last_run TEXT,
        PRIMARY KEY (industry, area))""")
    return conn


def latest_report(conn, industry, area, max_age_days=None):
    row = conn.execute("SELECT id, report, created FROM market_reports WHERE lower(industry)=? AND lower(area)=? "
                       "ORDER BY id DESC LIMIT 1", (industry.lower().strip(), area.lower().strip())).fetchone()
    if not row:
        return None
    if max_age_days is not None:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row[2])
        if age.total_seconds() > max_age_days * 86400:
            return None
    return row[0], json.loads(row[1])


def compare(industry, areas):
    """Bandingkan laporan terbaru tiap wilayah -> skor peluang."""
    conn = db()
    rows = []
    try:
        for area in areas:
            got = latest_report(conn, industry, area)
            if not got:
                continue
            rid, r = got
            s, d = r["summary"], r["digital"]
            n = max(s["businesses"], 1)
            rows.append({
                "area": area, "report_id": rid, "created": r["created"],
                "businesses": s["businesses"], "avg_rating": s["avg_rating"],
                "median_reviews": s["median_reviews"], "total_reviews": s["total_reviews"],
                "reviews_per_business": round(s["total_reviews"] / n),
                "top3_share": s["top3_share"], "gems": len(r.get("gems", [])), "weak": len(r.get("weak", [])),
                "website_pct": d["website"], "whatsapp_pct": d["whatsapp"],
                "prospects": len(r.get("prospects", [])), "capped": bool(s.get("capped")),
            })
    finally:
        conn.close()
    if not rows:
        return {"industry": industry, "areas": [], "insights": ["Belum ada laporan untuk wilayah ini."]}

    def norm(key, invert=False):
        vals = [x[key] or 0 for x in rows]
        lo, hi = min(vals), max(vals)
        for x in rows:
            v = ((x[key] or 0) - lo) / (hi - lo) if hi > lo else 0.5
            x.setdefault("_parts", {})[key] = 1 - v if invert else v

    # peluang = permintaan per pemain tinggi + pemain lebih sedikit + pasar tidak dikuasai + pemain lemah
    norm("reviews_per_business"); norm("businesses", invert=True)
    norm("top3_share", invert=True); norm("weak"); norm("avg_rating", invert=True)
    for x in rows:
        p = x.pop("_parts")
        x["opportunity"] = round(100 * (0.35 * p["reviews_per_business"] + 0.25 * p["businesses"] +
                                        0.15 * p["top3_share"] + 0.15 * p["weak"] + 0.10 * p["avg_rating"]))
    rows.sort(key=lambda x: -x["opportunity"])
    best, crowded = rows[0], max(rows, key=lambda x: x["businesses"])
    insights = [f"Peluang terbesar: {best['area']} (skor {best['opportunity']}), {best['businesses']} pemain, "
                f"±{best['reviews_per_business']} review per bisnis."]
    if crowded["area"] != best["area"]:
        insights.append(f"Paling padat: {crowded['area']} dengan {crowded['businesses']} pemain.")
    low_digital = min(rows, key=lambda x: x["website_pct"])
    insights.append(f"Kematangan digital terendah: {low_digital['area']} ({low_digital['website_pct']}% punya "
                    f"website), target paling empuk untuk jasa digital.")
    capped = [x["area"] for x in rows if x["capped"]]
    if capped:
        insights.append(f"Catatan: jumlah pemain di {', '.join(capped)} mentok di batas pencarian, angka sebenarnya bisa lebih banyak; riset ulang dengan --max lebih besar untuk perbandingan adil.")
    insights.append("Skor peluang = permintaan per bisnis (35%) + sedikit pesaing (25%) + pasar tidak dikuasai "
                    "pemain besar (15%) + banyak pemain lemah (15%) + rating rata-rata lebih rendah (10%).")
    return {"industry": industry, "areas": rows, "insights": insights}


def wa_link(phone: str) -> str:
    d = datastore.norm_phone(phone)
    # hanya nomor HP (62 8xx) yang bisa WhatsApp; telepon kantor tidak
    return f"https://wa.me/{d}" if datastore.is_mobile(d) else ""


LEAD_STATUSES = ("baru", "dihubungi", "tertarik", "deal", "tolak")


def lead_score(p):
    """Skor peluang jual 0-100 + alasannya. None kalau detail (telepon/website) belum diambil."""
    if not p.get("_detailed"):
        return None, []
    s, why = 0, []
    rating, reviews = p.get("rating") or 0, p.get("reviews") or 0
    # bisa dihubungi
    if wa_link(p.get("phone")):
        s += 25; why.append("bisa WhatsApp")
    elif p.get("phone"):
        s += 10; why.append("ada telepon")
    if p.get("emails"):
        s += 5; why.append("ada email")
    # celah digital
    if not p.get("website"):
        s += 20; why.append("tanpa website")
    if not p.get("instagram") or p.get("instagram_shared"):
        s += 15; why.append("IG tidak ditemukan")
    # bisnis sehat = mampu bayar dan layak dibantu
    if rating >= 4.5:
        s += 15; why.append(f"rating {rating}")
    elif rating >= 4.0:
        s += 8
    if 30 <= reviews <= 800:
        s += 15; why.append(f"{reviews} ulasan, aktif")
    elif reviews < 30:
        s += 5
    if (p.get("reviews_per_month") or 0) > 0:
        s += 5; why.append("sedang tumbuh")
    # bukan target: jaringan besar / pemain dominan
    if (p.get("branches_in_area") or 1) > 1:
        s -= 25; why.append("jaringan bercabang")
    if reviews > 800:
        s -= 40; why.append("pemain besar")
    if not (p.get("phone") or p.get("instagram") or p.get("emails")):
        s = min(s, 20); why.append("tidak ada kontak")
    return max(0, min(100, s)), why


def score_all(report):
    """Tempel lead_score ke semua bisnis di laporan (juga untuk laporan lama)."""
    for key in ("all", "prospects", "leaders", "gems", "weak", "growing"):
        for p in report.get(key) or []:
            p["lead_score"], p["lead_reasons"] = lead_score(p)
    return report


def lead_key(p):
    return p.get("place_id") or p.get("maps_url") or ""


def leads_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS leads (
        key TEXT PRIMARY KEY, name TEXT, status TEXT, note TEXT, updated TEXT)""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(leads)")}
    if "remind" not in cols:  # migrasi: tanggal pengingat follow up (YYYY-MM-DD)
        conn.execute("ALTER TABLE leads ADD COLUMN remind TEXT")
    return conn


def get_leads():
    conn = leads_db(db())
    try:
        return {k: {"status": s, "note": n or "", "updated": u, "remind": r or ""}
                for k, s, n, u, r in conn.execute("SELECT key, status, note, updated, remind FROM leads")}
    finally:
        conn.close()


def set_lead(key, name, status, note=None, remind=None):
    """Simpan status lead. note/remind None = pertahankan nilai lama."""
    if not key or status not in LEAD_STATUSES:
        raise ValueError("key/status tidak valid")
    if remind and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", remind):
        raise ValueError("format tanggal pengingat harus YYYY-MM-DD")
    conn = leads_db(db())
    try:
        old = conn.execute("SELECT note, remind FROM leads WHERE key=?", (key,)).fetchone() or ("", "")
        conn.execute("INSERT OR REPLACE INTO leads (key, name, status, note, updated, remind) VALUES (?,?,?,?,?,?)",
                     (key, name, status, old[0] if note is None else note,
                      datetime.now(timezone.utc).isoformat(), old[1] if remind is None else remind))
        conn.commit()
    finally:
        conn.close()


def latest_reports(conn):
    """Laporan terbaru per (industri, wilayah): [(id, report)]."""
    seen, out = set(), []
    for rid, ind, area, rep in conn.execute(
            "SELECT id, industry, area, report FROM market_reports ORDER BY id DESC"):
        k = (ind.lower().strip(), area.lower().strip())
        if k not in seen:
            seen.add(k)
            out.append((rid, json.loads(rep)))
    return out


def prospect_board(min_score=40):
    """Semua prospek dari laporan terbaru tiap wilayah + status follow up-nya, tanpa duplikat."""
    conn = db()
    try:
        reports = latest_reports(conn)
    finally:
        conn.close()
    leads = get_leads()
    best = {}
    for rid, r in reports:
        score_all(r)
        for p in r.get("all") or []:
            k = lead_key(p)
            if not k or p.get("lead_score") is None:
                continue
            if p["lead_score"] < min_score and k not in leads:
                continue
            item = dict(p, key=k, industry=r["industry"], area=r["area"], report_id=rid, lead=leads.get(k))
            if k not in best or item["lead_score"] > best[k]["lead_score"]:
                best[k] = item
    return sorted(best.values(), key=lambda x: -x["lead_score"])


def report_changes(prev, cur):
    """Apa yang berubah antara dua laporan wilayah yang sama."""
    old = {lead_key(p): p for p in prev.get("all") or []}
    new = {lead_key(p): p for p in cur.get("all") or []}
    out = {"new": [], "gone": [], "rating_up": [], "rating_down": [], "reviews_jump": []}
    for k, p in new.items():
        q = old.get(k)
        if not q:
            out["new"].append(p)
            continue
        dr = round((p.get("rating") or 0) - (q.get("rating") or 0), 1)
        if dr >= 0.1 and q.get("rating"):
            out["rating_up"].append(dict(p, delta=dr))
        elif dr <= -0.1 and q.get("rating"):
            out["rating_down"].append(dict(p, delta=dr))
        jump = (p.get("reviews") or 0) - (q.get("reviews") or 0)
        if jump >= max(10, 0.1 * (q.get("reviews") or 0)):
            out["reviews_jump"].append(dict(p, delta=jump))
    out["gone"] = [p for k, p in old.items() if k not in new]
    for k in out:
        out[k].sort(key=lambda p: -abs(p.get("delta") or p.get("reviews") or 0))
    return out


def watch_overview():
    """Wilayah yang dipantau + perubahan sejak riset sebelumnya."""
    conn = db()
    try:
        watches = {(a.lower().strip(), b.lower().strip()): {"industry": a, "area": b, "every_days": c, "last_run": d}
                   for a, b, c, d in conn.execute("SELECT industry, area, every_days, last_run FROM market_watches")}
        pairs = {(a.lower().strip(), b.lower().strip()): (a, b) for a, b in conn.execute(
            "SELECT DISTINCT industry, area FROM market_reports")}
        out = []
        for k, (ind, area) in pairs.items():
            rows = conn.execute("SELECT id, report FROM market_reports WHERE lower(industry)=? AND lower(area)=? "
                                "ORDER BY id DESC LIMIT 2", k).fetchall()
            cur = json.loads(rows[0][1])
            item = {"industry": ind, "area": area, "report_id": rows[0][0], "created": cur["created"],
                    "businesses": cur["summary"]["businesses"], "watch": watches.get(k), "runs": len(rows),
                    "changes": None}
            if len(rows) > 1:
                prev = json.loads(rows[1][1])
                item["prev_created"] = prev["created"]
                item["changes"] = report_changes(prev, cur)
            out.append(item)
    finally:
        conn.close()
    out.sort(key=lambda x: x["created"], reverse=True)
    out.sort(key=lambda x: x["watch"] is None)  # yang dipantau di atas, lalu terbaru
    return out


def detail_one(report_id, key):
    """Ambil telepon/website/IG satu bisnis dari Google Maps, lalu perbarui laporannya."""
    conn = db()
    try:
        row = conn.execute("SELECT report FROM market_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise ValueError("laporan tidak ada")
        rep = json.loads(row[0])
        p = next((x for x in rep.get("all") or [] if lead_key(x) == key), None)
        if not p or not p.get("maps_url"):
            raise ValueError("bisnis tidak ada di laporan")
        p.pop("_detailed", None)
        gmaps.details([p], limit=1)
        if not p.get("_detailed"):
            raise RuntimeError("Google Maps tidak merespons, coba lagi sebentar lagi")
        if p.get("website") and not p.get("instagram"):
            find_instagram([p], limit=1)
        p.update({k: v for k, v in (datastore.clean_place(dict(p)) or {}).items()
                  if k in ("phone", "website", "instagram")})
        if p.get("phone"):
            p["whatsapp"] = wa_link(p["phone"])
        conn.execute("UPDATE market_reports SET report=? WHERE id=?", (json.dumps(rep, ensure_ascii=False), report_id))
        if p.get("place_id"):
            got = conn.execute("SELECT data FROM places WHERE place_id=?", (p["place_id"],)).fetchone()
            if got:
                d = json.loads(got[0])
                d.update({k: p[k] for k in ("phone", "website", "instagram", "_detailed") if p.get(k)})
                conn.execute("UPDATE places SET data=? WHERE place_id=?", (json.dumps(d, ensure_ascii=False), p["place_id"]))
        conn.commit()
    finally:
        conn.close()
    p["lead_score"], p["lead_reasons"] = lead_score(p)
    return p


def find_instagram(places, limit=25):
    """Website / halaman link-in-bio -> akun IG."""
    from competitor_website_monitor import scan
    todo = [p for p in places if p.get("website") and not p.get("instagram")][:limit]
    for p in todo:
        try:
            d = scan(p["website"])
            igs = d.get("ig_links") or []
            if igs:
                p["instagram"] = igs[0]
            if d.get("emails"):
                p["emails"] = d["emails"][:3]
        except Exception:
            pass
        time.sleep(1)


def load_previous(conn, places):
    """Gabungkan data detail lama (phone/website/IG) + hitung momentum review."""
    now = datetime.now(timezone.utc)
    for p in places:
        row = conn.execute("SELECT data, history FROM places WHERE place_id=?", (p["place_id"],)).fetchone()
        p["history"] = json.loads(row[1]) if row and row[1] else []
        if row:
            old = json.loads(row[0])
            for k in ("phone", "website", "instagram", "emails", "_detailed"):
                if old.get(k) and not p.get(k):
                    p[k] = old[k]
        prev = [h for h in p["history"] if h.get("reviews")]
        if prev:
            first = prev[0]
            days = max((now - datetime.fromisoformat(first["ts"])).total_seconds() / 86400, 0.01)
            p["reviews_delta"] = p["reviews"] - first["reviews"]
            p["reviews_per_month"] = round(p["reviews_delta"] / days * 30, 1) if days >= 1 else None
            p["tracked_days"] = round(days, 1)


def save_places(conn, places):
    now = datetime.now(timezone.utc).isoformat()
    for p in places:
        hist = datastore.snapshot(p.pop("history", []), {"reviews": p["reviews"], "rating": p["rating"]})
        for k in ("brand_group",):  # field turunan per-batch, tidak disimpan
            p.pop(k, None)
        conn.execute("""INSERT INTO places VALUES (?,?,?,?) ON CONFLICT(place_id) DO UPDATE SET
            data=excluded.data, history=excluded.history, last_seen=excluded.last_seen""",
                     (p["place_id"], json.dumps(p, ensure_ascii=False), json.dumps(hist[-60:]), now))
    conn.commit()


def pct(n, d):
    return round(n / d * 100) if d else 0


def build_report(industry, area, places):
    n = len(places)
    rated = [p for p in places if p.get("rating")]
    reviews = [p["reviews"] for p in places]
    total_rev = sum(reviews) or 1
    by_rev = sorted(places, key=lambda p: -p["reviews"])
    detailed = [p for p in places if p.get("_detailed")]

    shares = [r / total_rev for r in reviews]
    hhi = round(sum(s * s for s in shares) * 10000)
    top3 = pct(sum(p["reviews"] for p in by_rev[:3]), total_rev)
    top10 = pct(sum(p["reviews"] for p in by_rev[:10]), total_rev)
    conc = ("terkonsentrasi (dikuasai sedikit pemain)" if hhi > 2500 else
            "cukup terkonsentrasi" if hhi > 1500 else "terfragmentasi (banyak pemain kecil)")

    def slim(p, extra=()):
        keys = ("place_id", "name", "rating", "reviews", "category", "address", "maps_url", "instagram",
                "website", "phone", "reviews_per_month", "status", "emails", "price", "dist_km",
                "instagram_shared", "website_shared", "branches_in_area", "lat", "lon",
                "_detailed") + tuple(extra)
        d = {k: p.get(k) for k in keys if p.get(k) not in (None, "")}
        if p.get("phone"):
            d["whatsapp"] = wa_link(p["phone"])
        return d

    gems = sorted([p for p in rated if p["rating"] >= 4.7 and 15 <= p["reviews"] < 300],
                  key=lambda p: -(p["rating"] * math.log10(p["reviews"] + 1)))
    weak = sorted([p for p in rated if p["rating"] < 4.3 and p["reviews"] >= 150],
                  key=lambda p: p["rating"])
    growing = sorted([p for p in places if (p.get("reviews_per_month") or 0) > 0],
                     key=lambda p: -p["reviews_per_month"])
    # prospek: bisnis kecil yang bagus tapi lemah digital -> bisa ditawari bantuan
    prospects = []
    for p in places:
        if not p.get("_detailed") or not p.get("rating"):
            continue
        gaps = []
        if not p.get("website"):
            gaps.append("tanpa website")
        if not p.get("instagram"):
            gaps.append("IG tidak ditemukan")
        if p["reviews"] < 100:
            gaps.append("review masih sedikit")
        if p["rating"] < 4.3:
            gaps.append(f"rating {p['rating']}")
        if gaps and p["reviews"] <= 800 and (p.get("phone") or p.get("instagram")):
            score, _ = lead_score(p)
            prospects.append(dict(slim(p), gaps=gaps, score=score))
    prospects.sort(key=lambda x: (-x["score"], -x.get("rating", 0)))

    cats = Counter(p.get("category") or "Lainnya" for p in places)
    rating_buckets = Counter(
        "4.5+" if p["rating"] >= 4.5 else "4.0–4.4" if p["rating"] >= 4.0 else "3.5–3.9" if p["rating"] >= 3.5 else "<3.5"
        for p in rated)
    size_buckets = Counter(
        "1000+" if r >= 1000 else "200–999" if r >= 200 else "50–199" if r >= 50 else "<50" for r in reviews)

    digital = {
        "sampled": len(detailed),
        "website": pct(sum(1 for p in detailed if p.get("website")), len(detailed)),
        "instagram": pct(sum(1 for p in detailed if p.get("instagram")), len(detailed)),
        "phone": pct(sum(1 for p in detailed if p.get("phone")), len(detailed)),
        "whatsapp": pct(sum(1 for p in detailed if wa_link(p.get("phone"))), len(detailed)),
    }

    insights = []
    if n:
        insights.append(f"{n} bisnis {industry} ditemukan di {area} (batas bawah, hanya yang muncul di Google Maps).")
        insights.append(f"Pasar {conc}: 3 teratas memegang {top3}% dari seluruh review, 10 teratas {top10}%.")
        if rated:
            avg = statistics.mean(p["rating"] for p in rated)
            insights.append(f"Rating rata-rata {avg:.2f}. Standar pasar tinggi, di bawah 4,5 sudah terlihat kalah."
                            if avg >= 4.5 else f"Rating rata-rata {avg:.2f}, ada ruang untuk bersaing lewat kualitas layanan.")
        if size_buckets.get("<50", 0) / n > 0.4:
            insights.append(f"{pct(size_buckets['<50'], n)}% pemain masih kecil (<50 review): pasar banyak pendatang baru.")
        if weak:
            insights.append(f"{len(weak)} pemain ramai tapi rating < 4,3 (mis. {weak[0]['name']}): pelanggan mereka "
                            f"tidak puas, peluang merebut pasar dengan kualitas.")
        if gems:
            insights.append(f"{len(gems)} hidden gem (rating ≥4,7, review <300), calon pemain yang sedang naik.")
        if detailed:
            insights.append(f"Kematangan digital: {digital['website']}% punya website, {digital['instagram']}% IG "
                            f"terdeteksi, {digital['whatsapp']}% bisa dihubungi via WhatsApp (dari {len(detailed)} sampel).")
        if growing:
            g = growing[0]
            insights.append(f"Tumbuh paling cepat: {g['name']} (+{g['reviews_per_month']} review/bulan).")
        else:
            insights.append("Momentum (review/bulan) muncul setelah wilayah ini dipindai ulang beberapa hari lagi.")

    return {
        "industry": industry, "area": area, "created": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "businesses": n, "total_reviews": sum(reviews),
            "avg_rating": round(statistics.mean(p["rating"] for p in rated), 2) if rated else None,
            "median_reviews": int(statistics.median(reviews)) if reviews else 0,
            "hhi": hhi, "top3_share": top3, "top10_share": top10, "concentration": conc,
        },
        "insights": insights,
        "categories": cats.most_common(8),
        "rating_buckets": [[k, rating_buckets.get(k, 0)] for k in ("4.5+", "4.0–4.4", "3.5–3.9", "<3.5")],
        "size_buckets": [[k, size_buckets.get(k, 0)] for k in ("1000+", "200–999", "50–199", "<50")],
        "digital": digital,
        "leaders": [slim(p) for p in by_rev[:10]],
        "gems": [slim(p) for p in gems[:10]],
        "weak": [slim(p) for p in weak[:8]],
        "growing": [slim(p) for p in growing[:10]],
        "prospects": prospects[:25],
        "all": [slim(p) for p in by_rev],
    }


def classify_categories(industry, places):
    """Nilai relevansi tiap kategori Maps untuk industri ini: cache DB -> AI -> aturan."""
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS category_relevance (
        industry TEXT, category TEXT, verdict TEXT, source TEXT, PRIMARY KEY (industry, category))""")
    key = industry.lower().strip()
    cats = sorted({p.get("category") or "" for p in places} - {""})
    verdicts = {c: v for c, v in conn.execute(
        "SELECT category, verdict FROM category_relevance WHERE industry=?", (key,)) if c in cats}
    todo = [c for c in cats if c not in verdicts]
    source = "rules"
    if todo and os.getenv("OPENROUTER_API_KEY"):
        try:
            import agent
            import netutil
            examples = {c: [p["name"] for p in places if p.get("category") == c][:3] for c in todo}
            prompt = (f"Industri yang diriset: \"{industry}\". Untuk tiap kategori Google Maps di bawah, nilai apakah "
                      "bisnis di kategori itu termasuk industri tersebut (pesaing langsung). Jawab JSON saja: "
                      '{"kategori": "yes"|"no"|"maybe"}. "maybe" = kategori campuran/umum yang perlu dilihat '
                      "dari nama bisnisnya.\n\n" + json.dumps(examples, ensure_ascii=False))
            for model in agent.candidate_models()[:3]:
                r = netutil.post("https://openrouter.ai/api/v1/chat/completions", retries=1, timeout=60,
                                 headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                                          "Content-Type": "application/json"},
                                 json={"model": model, "temperature": 0,
                                       "messages": [{"role": "user", "content": prompt}]})
                j = r.json()
                content = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if content:
                    m = re.search(r"\{.*\}", content, re.S)
                    got = json.loads(m.group(0)) if m else {}
                    got = {c: v for c, v in got.items() if c in todo and v in ("yes", "no", "maybe")}
                    if got:
                        verdicts.update(got)
                        source = "ai"
                        break
        except Exception as e:
            log.info(f"      klasifikasi AI gagal, pakai aturan: {e}")
    rest = [c for c in todo if c not in verdicts]
    verdicts.update(datastore.rule_verdicts(industry, rest))
    # aturan kata kunci menang kalau bilang relevan (AI kadang terlalu ketat, mis. "Kafe" untuk coffee shop)
    ai_said = dict(verdicts)
    for c, v in datastore.rule_verdicts(industry, todo).items():
        if v == "yes" and ai_said.get(c) != "no":  # AI tegas "no" tetap dihormati
            verdicts[c] = "yes"
    # kata inti industri ada di nama kategori (mis. "kopi" di "Tempat penyangraian kopi") = pasti relevan
    core = datastore.INDUSTRY_ALIASES.get(datastore.industry_group(industry), [])
    for c in todo:
        if any(w.strip() and w.strip() in c.lower() for w in core):
            verdicts[c] = "yes"
    for c in todo:
        conn.execute("INSERT OR REPLACE INTO category_relevance VALUES (?,?,?,?)",
                     (key, c, verdicts[c], source if c not in rest else "rules"))
    conn.commit()
    conn.close()
    return verdicts


def llm_insights(report):
    """Narasi strategi dari LLM (opsional, kalau OPENROUTER_API_KEY ada)."""
    if not os.getenv("OPENROUTER_API_KEY"):
        return None
    try:
        import agent
        import netutil
        data = {k: report[k] for k in ("industry", "area", "summary", "insights", "digital",
                                       "rating_buckets", "size_buckets")}
        data["leaders"] = [{k: p.get(k) for k in ("name", "rating", "reviews")} for p in report["leaders"][:5]]
        data["gems"] = [{k: p.get(k) for k in ("name", "rating", "reviews")} for p in report["gems"][:5]]
        data["weak"] = [{k: p.get(k) for k in ("name", "rating", "reviews")} for p in report["weak"][:5]]
        prompt = ("Kamu analis pasar. Dari data riset pasar Google Maps berikut, tulis 4–6 poin strategi "
                  "konkret dalam bahasa Indonesia untuk pemilik bisnis baru/kecil di industri dan wilayah ini "
                  "(positioning, celah, cara menang). Satu kalimat per poin, mulai dengan '- '. Jangan mengarang "
                  "angka di luar data.\n\n" + json.dumps(data, ensure_ascii=False))
        for model in agent.candidate_models()[:4]:
            r = netutil.post("https://openrouter.ai/api/v1/chat/completions", retries=1, timeout=90,
                             headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                                      "Content-Type": "application/json"},
                             json={"model": model, "temperature": 0.4,
                                   "messages": [{"role": "user", "content": prompt}]})
            j = r.json()
            content = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if content:
                lines = [l.strip()[2:].strip() for l in content.splitlines()
                         if l.strip().startswith(("- ", "* ", "• "))]
                return [l.replace(" \u2014 ", ", ").replace("\u2014", ", ") for l in lines] or None
    except Exception as e:
        log.info(f"LLM insight gagal: {e}")
    return None


def run(industry, area, max_results=120, detail_limit=30):
    t0 = time.time()
    log.info(f"[1/5] Cari '{industry}' di '{area}' (Google Maps)…")
    places = gmaps.search(f"{industry} {area}", max_results=max_results)
    if not places:
        raise SystemExit("Tidak ada hasil dari Google Maps. Coba kata kunci lain.")
    places, quality = datastore.clean_batch(places)
    log.info(f"      bersihkan: {quality['raw']} mentah -> {quality['kept']} bersih "
             f"(tidak valid/tutup {quality['invalid_or_closed']}, di luar wilayah {quality['out_of_area']}, "
             f"duplikat digabung {quality['duplicates_merged']})")
    verdicts = classify_categories(industry, places)
    places, irrelevant = datastore.apply_relevance(places, industry, verdicts)
    quality["irrelevant"] = len(irrelevant)
    quality["irrelevant_examples"] = [f"{p['name']} ({p.get('category') or '-'})" for p in irrelevant[:6]]
    quality["kept"] = len(places)
    log.info(f"      relevansi: {len(irrelevant)} bukan '{industry}' dibuang "
             f"({', '.join(quality['irrelevant_examples'][:3]) or '-'})")
    if not places:
        raise SystemExit("Semua hasil tersaring (tidak valid / di luar wilayah / tidak relevan).")
    conn = db()
    load_previous(conn, places)
    # detail: pemimpin pasar + bisnis kecil (calon prospek), yang belum pernah di-detail
    by_rev = sorted(places, key=lambda p: -p["reviews"])
    small = [p for p in places if 10 <= p["reviews"] <= 800]
    pick, seen = [], set()
    for p in by_rev[:detail_limit // 3] + small:
        if p["place_id"] not in seen and not p.get("_detailed"):
            pick.append(p); seen.add(p["place_id"])
    pick = pick[:detail_limit]
    log.info(f"[2/5] {len(places)} bisnis. Ambil detail (telepon/website/IG) {len(pick)} tempat…")
    gmaps.details(pick, limit=len(pick))
    log.info("[3/5] Cari akun IG dari website / link-in-bio…")
    find_instagram(places)
    # normalisasi ulang field yang baru didapat (HP, website, IG) + tandai akun bersama/cabang
    places = [datastore.clean_place(p) or p for p in places]
    datastore.flag_shared(places)
    quality["chains"] = len({p["brand_group"] for p in places if p.get("branches_in_area", 1) > 1})
    quality["brands"] = len({p["brand_group"] for p in places})
    log.info("[4/5] Susun laporan…")
    report = score_all(build_report(industry, area, places))
    report["data_quality"] = quality
    report["summary"]["capped"] = len(places) >= max_results  # jumlah bisnis mentok di batas
    ai = llm_insights(report)
    if ai:
        report["ai_strategy"] = ai
    save_places(conn, places)
    cur = conn.execute("INSERT INTO market_reports (industry, area, created, report) VALUES (?,?,?,?)",
                       (industry, area, report["created"], json.dumps(report, ensure_ascii=False)))
    conn.execute("UPDATE market_watches SET last_run=? WHERE lower(industry)=? AND lower(area)=?",
                 (report["created"], industry.lower().strip(), area.lower().strip()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    log.info(f"[5/5] Selesai dalam {int(time.time() - t0)} detik. REPORT_ID={rid}")
    return rid, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", required=True)
    ap.add_argument("--area", help="satu wilayah")
    ap.add_argument("--areas", help="beberapa wilayah dipisah ';' untuk dibandingkan")
    ap.add_argument("--reuse-days", type=int, default=7,
                    help="mode --areas: pakai laporan wilayah yang umurnya < N hari (tidak riset ulang)")
    ap.add_argument("--max", type=int, default=120, help="maks bisnis dari Google Maps")
    ap.add_argument("--details", type=int, default=30, help="jumlah tempat yang diambil telepon/website-nya")
    args = ap.parse_args()
    if args.areas:
        areas = [a.strip() for a in args.areas.split(";") if a.strip()][:6]
        conn = db()
        todo = [a for a in areas if not latest_report(conn, args.industry, a, args.reuse_days)]
        conn.close()
        log.info(f"[compare] {len(areas)} wilayah, {len(areas) - len(todo)} pakai laporan tersimpan, "
                 f"{len(todo)} riset baru")
        for i, a in enumerate(todo, 1):
            log.info(f"[compare] wilayah {i}/{len(todo)}: {a}")
            try:
                run(args.industry, a, args.max, args.details)
            except SystemExit as e:
                log.info(f"  {a}: {e}")
        res = compare(args.industry, areas)
        print("\n".join("• " + i for i in res["insights"]))
        log.info("COMPARE_DONE")
        return
    if not args.area:
        ap.error("butuh --area atau --areas")
    rid, rep = run(args.industry, args.area, args.max, args.details)
    print("\n".join("• " + i for i in rep["insights"]))


if __name__ == "__main__":
    main()
