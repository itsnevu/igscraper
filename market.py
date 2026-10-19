"""
market.py — engine riset pasar per industri x wilayah.

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
    return conn


def wa_link(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("0"):
        d = "62" + d[1:]
    # hanya nomor HP (62 8xx) yang bisa WhatsApp; telepon kantor tidak
    return f"https://wa.me/{d}" if d.startswith("628") and 10 <= len(d) <= 15 else ""


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
        hist = p.pop("history", [])
        hist.append({"ts": now, "reviews": p["reviews"], "rating": p["rating"]})
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
        keys = ("name", "rating", "reviews", "category", "address", "maps_url", "instagram",
                "website", "phone", "reviews_per_month", "status") + tuple(extra)
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
            score = len(gaps) + (1 if wa_link(p.get("phone")) else 0) + (1 if p["rating"] >= 4.5 else 0)
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
        insights.append(f"{n} bisnis {industry} ditemukan di {area} (batas bawah — hanya yang muncul di Google Maps).")
        insights.append(f"Pasar {conc}: 3 teratas memegang {top3}% dari seluruh review, 10 teratas {top10}%.")
        if rated:
            avg = statistics.mean(p["rating"] for p in rated)
            insights.append(f"Rating rata-rata {avg:.2f}. Standar pasar tinggi — di bawah 4,5 sudah terlihat kalah."
                            if avg >= 4.5 else f"Rating rata-rata {avg:.2f} — ada ruang untuk bersaing lewat kualitas layanan.")
        if size_buckets.get("<50", 0) / n > 0.4:
            insights.append(f"{pct(size_buckets['<50'], n)}% pemain masih kecil (<50 review): pasar banyak pendatang baru.")
        if weak:
            insights.append(f"{len(weak)} pemain ramai tapi rating < 4,3 (mis. {weak[0]['name']}): pelanggan mereka "
                            f"tidak puas — peluang merebut pasar dengan kualitas.")
        if gems:
            insights.append(f"{len(gems)} hidden gem (rating ≥4,7, review <300) — calon pemain yang sedang naik.")
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
            if j.get("choices"):
                lines = [l.strip()[2:].strip() for l in j["choices"][0]["message"]["content"].splitlines()
                         if l.strip().startswith(("- ", "* ", "• "))]
                return lines or None
    except Exception as e:
        log.info(f"LLM insight gagal: {e}")
    return None


def run(industry, area, max_results=80, detail_limit=30):
    t0 = time.time()
    log.info(f"[1/5] Cari '{industry}' di '{area}' (Google Maps)…")
    places = gmaps.search(f"{industry} {area}", max_results=max_results)
    if not places:
        raise SystemExit("Tidak ada hasil dari Google Maps. Coba kata kunci lain.")
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
    log.info("[4/5] Susun laporan…")
    report = build_report(industry, area, places)
    ai = llm_insights(report)
    if ai:
        report["ai_strategy"] = ai
    save_places(conn, places)
    cur = conn.execute("INSERT INTO market_reports (industry, area, created, report) VALUES (?,?,?,?)",
                       (industry, area, report["created"], json.dumps(report, ensure_ascii=False)))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    log.info(f"[5/5] Selesai dalam {int(time.time() - t0)} detik. REPORT_ID={rid}")
    return rid, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", required=True)
    ap.add_argument("--area", required=True)
    ap.add_argument("--max", type=int, default=80, help="maks bisnis dari Google Maps")
    ap.add_argument("--details", type=int, default=30, help="jumlah tempat yang diambil telepon/website-nya")
    args = ap.parse_args()
    rid, rep = run(args.industry, args.area, args.max, args.details)
    print("\n".join("• " + i for i in rep["insights"]))


if __name__ == "__main__":
    main()
