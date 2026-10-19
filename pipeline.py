"""
pipeline.py, jalankan semua scraping kompetitor lokal dalam satu perintah.

Alur:
  1. Lokasi -> koordinat (Nominatim / OpenStreetMap, gratis)
  2. Cari bisnis kategori X dalam radius (Overpass / OSM, gratis)
  3. Scan website tiap bisnis -> link IG, email, harga
  4. Statistik IG dari Social Blade (followers, ER, grade)
  5. Scrape IG langsung (ig_scraper.py) untuk N akun teratas
  6. Google Trends untuk 5 nama teratas
  7. Simpan ke ig.db (tabel businesses + riwayat) dan hasil/competitors.csv

Usage:
  python pipeline.py --location "Kemang, Jakarta Selatan" --category cafe
  python pipeline.py --location "Dago, Bandung" --category restaurant --radius 3000 --ig-scrape 5
  python pipeline.py --location "Jogja" --category "shop=clothes"
"""

import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

import base64
import html as htmllib

import netutil as creq  # curl_cffi + DNS-over-HTTPS (get/post kompatibel)
from competitor_website_monitor import scan as scan_website
from socialblade_growth import growth as socialblade

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("pipeline")

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "ig.db")
UA = {"User-Agent": "local-competitor-scanner/0.1 (personal research)"}

CATEGORIES = {
    "cafe": '"amenity"="cafe"',
    "restaurant": '"amenity"="restaurant"',
    "fast_food": '"amenity"="fast_food"',
    "bakery": '"shop"="bakery"',
    "clothes": '"shop"="clothes"',
    "beauty": '"shop"="beauty"',
    "salon": '"shop"="hairdresser"',
    "gym": '"leisure"="fitness_centre"',
    "laundry": '"shop"="laundry"',
    "pharmacy": '"amenity"="pharmacy"',
    "coworking": '"amenity"="coworking_space"',
    "hotel": '"tourism"="hotel"',
    "shop": '"shop"',
    # industri jasa / kantor (nilai list = gabungan beberapa filter OSM)
    "agency": ['"office"="advertising_agency"', '"office"="marketing"',
               '"office"~"company|it|media"]["name"~"agency|digital|creative|kreatif|studio|media|marketing",i'],
    "it_software": ['"office"="it"', '"office"="telecommunication"',
                    '"office"="company"]["name"~"software|tech|teknologi|digital|solusi|solution",i'],
    "consulting": ['"office"="consulting"', '"office"="company"]["name"~"consult|konsultan",i'],
    "law": ['"office"="lawyer"', '"office"="notary"'],
    "accounting": ['"office"="accountant"', '"office"="tax_advisor"'],
    "property": ['"office"="estate_agent"', '"office"="property_management"'],
    "clinic": ['"amenity"="clinic"', '"healthcare"="clinic"'],
    "dentist": ['"amenity"="dentist"', '"healthcare"="dentist"'],
    "education": ['"amenity"="school"', '"amenity"="language_school"', '"amenity"="training"',
                  '"office"="educational_institution"'],
    "car_repair": ['"shop"="car_repair"', '"shop"="motorcycle_repair"'],
    "printing": ['"shop"="copyshop"', '"craft"="printer"'],
    "electronics": ['"shop"="electronics"', '"shop"="mobile_phone"', '"shop"="computer"'],
    "furniture": ['"shop"="furniture"', '"shop"="interior_decoration"'],
    "pet": ['"shop"="pet"', '"amenity"="veterinary"'],
}
SEARCHABLE_KEYS = ["office", "shop", "amenity", "craft", "healthcare"]
OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
IG_URL_RE =re.compile(r"instagram\.com/([A-Za-z0-9_.]+)")
IG_SKIP = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about", "legal"}


# ------------------------------------------------------------
# 1 + 2. OSM
# ------------------------------------------------------------

def geocode(location):
    r = creq.get("https://nominatim.openstreetmap.org/search",
                 params={"q": location, "format": "json", "limit": 1, "countrycodes": "id"},
                 headers=UA, timeout=20)
    res = r.json()
    if not res:
        sys.exit(f"Lokasi tidak ditemukan: {location}")
    log.info(f"[1/7] Lokasi: {res[0]['display_name'][:80]}")
    return float(res[0]["lat"]), float(res[0]["lon"])


def osm_filters(category):
    """Kategori -> list filter Overpass (digabung OR)."""
    if category in CATEGORIES:
        f = CATEGORIES[category]
        return f if isinstance(f, list) else [f]
    if category.startswith("keyword:"):  # bebas: cari di nama bisnis
        kw = re.sub(r"[^\w\s|-]", "", category.split(":", 1)[1]).strip()
        if not kw:
            sys.exit("keyword kosong")
        rx = "|".join(w for w in re.split(r"\s*\|\s*", kw) if w)
        return [f'"{k}"]["name"~"{rx}",i' for k in SEARCHABLE_KEYS]
    if "=" in category:  # custom, mis. shop=clothes
        k, v = category.split("=", 1)
        return [f'"{k.strip()}"="{v.strip()}"']
    sys.exit(f"Kategori tidak dikenal: {category}. Pilihan: {', '.join(CATEGORIES)}, "
             f"key=value, atau keyword:kata kunci")


def find_businesses(lat, lon, category, radius):
    parts = "".join(f"nwr[{f}][name](around:{radius},{lat},{lon});" for f in osm_filters(category))
    q = f"[out:json][timeout:60];({parts});out center tags;"
    elements = None
    for attempt, server in enumerate(OVERPASS_SERVERS * 2):  # server publik sering sibuk -> coba mirror
        try:
            r = creq.post(server, data={"data": q}, headers=UA, timeout=90)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                elements = r.json().get("elements", [])
                break
            log.info(f"  Overpass {server.split('/')[2]}: HTTP {r.status_code}, coba server lain")
        except Exception as e:
            log.info(f"  Overpass {server.split('/')[2]}: {e}")
        time.sleep(5 * (attempt + 1))
    if elements is None:
        sys.exit("Semua server Overpass sibuk. Coba lagi beberapa menit lagi.")
    out = []
    for e in elements:
        t = e.get("tags", {})
        ig = t.get("contact:instagram") or t.get("instagram") or ""
        m = IG_URL_RE.search(ig)
        ig = (m.group(1) if m else ig).strip().lstrip("@").rstrip("/")
        street = " ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x)
        out.append({
            "osm_id": f"{e['type']}/{e['id']}",
            "name": t.get("name", ""),
            "brand": t.get("brand", ""),
            "category": category,
            "lat": e.get("lat") or e.get("center", {}).get("lat"),
            "lon": e.get("lon") or e.get("center", {}).get("lon"),
            "address": street or t.get("addr:full", ""),
            "website": t.get("website") or t.get("contact:website") or "",
            "phone": t.get("phone") or t.get("contact:phone") or "",
            "opening_hours": t.get("opening_hours", ""),
            "instagram": ig,
        })
    log.info(f"[2/7] {len(out)} bisnis '{category}' dalam {radius} m")
    return out


# ------------------------------------------------------------
# 3. website
# ------------------------------------------------------------

def enrich_websites(biz):
    with_site = [b for b in biz if b["website"]]
    log.info(f"[3/7] Scan {len(with_site)} website")
    cache = {}
    for b in with_site:
        url = b["website"] if b["website"].startswith("http") else "https://" + b["website"]
        if url not in cache:  # cabang brand yang sama sering pakai website yang sama
            cache[url] = scan_website(url)
            time.sleep(1.5)
        d = cache[url]
        b["emails"] = d.get("emails", [])
        b["prices"] = d.get("prices", [])[:5]
        igs = [u for u in d.get("ig_links", []) if u.lower() not in IG_SKIP]
        if not b["instagram"] and igs:
            b["instagram"] = igs[0]


def bing_instagram(query):
    """Cari akun IG lewat Bing (DuckDuckGo diblokir ISP + anti-bot)."""
    t = creq.get("https://www.bing.com/search", params={"q": query}).text
    urls = []
    for u in re.findall(r'[?&;]u=a1([A-Za-z0-9_\-]+)', t):  # link hasil di-base64 oleh Bing
        try:
            urls.append(base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode())
        except Exception:
            pass
    urls += re.findall(r"<cite>([^<]+)</cite>", t)
    found = []
    for x in urls:
        m = re.search(r"instagram\.com(?:\s›\s|/)([A-Za-z0-9_.]+)", htmllib.unescape(x))
        if (m and len(m.group(1).strip("._")) >= 3
                and m.group(1).lower() not in IG_SKIP | {"popular"} and m.group(1) not in found):
            found.append(m.group(1))
    return found


def enrich_search(biz, location):
    todo = [b for b in biz if not b["instagram"]]
    log.info(f"[3b/7] Cari IG via Bing untuk {len(todo)} bisnis tanpa IG")
    city = location.split(",")[-1].strip()
    cache = {}
    for b in todo:
        nm = (b["brand"] or b["name"]).strip()
        key = nm.lower()
        if key not in cache:
            try:
                cache[key] = bing_instagram(f"{nm} {city} instagram")
            except Exception as e:
                log.info(f"  {nm}: gagal ({e})")
                cache[key] = []
            time.sleep(3)
        if cache[key]:
            b["instagram"] = cache[key][0]
            b["instagram_source"] = "bing"  # tebakan pencarian; cek manual
    log.info(f"  -> {sum(1 for b in todo if b['instagram'])} ketemu")


# ------------------------------------------------------------
# 4. Social Blade
# ------------------------------------------------------------

def enrich_socialblade(biz):
    usernames = sorted({b["instagram"].lower() for b in biz if b["instagram"]})
    log.info(f"[4/7] Social Blade untuk {len(usernames)} akun IG")
    stats = {}
    for u in usernames:
        try:
            stats[u] = socialblade(u) or {}
        except Exception as e:
            log.info(f"  @{u}: gagal ({e})")
            stats[u] = {}
        time.sleep(3)
    for b in biz:
        s = stats.get(b["instagram"].lower(), {}) if b["instagram"] else {}
        b["followers"] = s.get("followers", 0)
        b["engagement_rate"] = s.get("engagement_rate", 0)
        b["avg_likes"] = s.get("avg_likes", 0)
        b["media_count"] = s.get("media_count", 0)
        b["grade"] = s.get("grade", "")


# ------------------------------------------------------------
# 5. IG scrape langsung
# ------------------------------------------------------------

def scrape_ig(biz, n, mode):
    if n <= 0:
        log.info("[5/7] Scrape IG dilewati (--ig-scrape 0)")
        return
    top = sorted({b["instagram"] for b in biz if b["instagram"]},
                 key=lambda u: -max(b["followers"] for b in biz if b["instagram"] == u))[:n]
    if not top:
        log.info("[5/7] Tidak ada akun IG untuk di-scrape")
        return
    log.info(f"[5/7] Scrape IG langsung: {', '.join('@' + u for u in top)}")
    subprocess.run([sys.executable, "-W", "ignore", "ig_scraper.py", "--mode", mode,
                    "--targets", ",".join(top), "--out", "hasil"], cwd=HERE)
    merge_ig_db(biz)


def merge_ig_db(biz):
    """Isi followers/ER dari hasil ig_scraper (tabel profiles) kalau Social Blade kosong."""
    if not os.path.exists(DB):
        return
    conn = sqlite3.connect(DB)
    try:
        for b in biz:
            if not b["instagram"] or b["followers"]:
                continue
            row = conn.execute("SELECT data FROM profiles WHERE lower(username)=?",
                               (b["instagram"].lower(),)).fetchone()
            if row:
                d = json.loads(row[0])
                b["followers"] = d.get("followers", 0)
                b["engagement_rate"] = (d.get("stats") or {}).get("engagement_rate", 0)
                b["avg_likes"] = (d.get("stats") or {}).get("avg_likes", 0)
                b["media_count"] = d.get("posts_count", 0)
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


# ------------------------------------------------------------
# 6. Google Trends
# ------------------------------------------------------------

def trends(biz, timeframe="today 3-m"):
    names = []
    for b in sorted(biz, key=lambda b: -b["followers"]):
        nm = (b["brand"] or b["name"]).strip()
        if nm and nm.lower() not in [x.lower() for x in names]:
            names.append(nm)
        if len(names) == 5:
            break
    if not names:
        return {}
    log.info(f"[6/7] Google Trends: {names}")
    try:
        from google_trends import trends as gt
        df = gt(names, timeframe)
        if df.empty:
            return {}
        df = df.drop(columns=["isPartial"], errors="ignore")
        # rata-rata 4 minggu terakhir vs 4 minggu sebelumnya
        out = {}
        for c in df.columns:
            recent, prev = df[c].tail(28).mean(), df[c].iloc[-56:-28].mean()
            out[c] = {"avg_recent": round(float(recent), 1),
                      "trend_pct": round(float((recent - prev) / prev * 100), 1) if prev else None}
        return out
    except Exception as e:
        log.info(f"  Trends gagal: {e}")
        return {}


def load_fresh(biz, hours):
    """Data hasil enrich yang dipindai < hours jam lalu, per osm_id."""
    if hours <= 0 or not os.path.exists(DB):
        return {}
    keep = ("instagram", "instagram_source", "emails", "prices", "followers", "engagement_rate",
            "avg_likes", "media_count", "grade")
    conn = sqlite3.connect(DB)
    out = {}
    try:
        ids = [b["osm_id"] for b in biz]
        rows = conn.execute(f"SELECT osm_id, data, last_scanned FROM businesses WHERE osm_id IN "
                            f"({','.join('?' * len(ids))})", ids).fetchall()
        now = datetime.now(timezone.utc)
        for oid, data, scanned in rows:
            try:
                age = (now - datetime.fromisoformat(scanned)).total_seconds() / 3600
            except (TypeError, ValueError):
                continue
            d = json.loads(data)
            # yang dulu gagal (followers 0 padahal punya IG) diproses ulang
            if age < hours and not (d.get("instagram") and not d.get("followers")):
                out[oid] = {k: d.get(k) for k in keep if k in d}
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return out


# ------------------------------------------------------------
# 7. simpan
# ------------------------------------------------------------

def save(biz, trend, location, out_dir="hasil"):
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS businesses (
        osm_id TEXT PRIMARY KEY, location TEXT, data TEXT,
        last_scanned TEXT, history TEXT)""")
    for b in biz:
        nm = (b["brand"] or b["name"]).strip()
        b["search_trend"] = trend.get(nm, {})
        row = conn.execute("SELECT history FROM businesses WHERE osm_id=?", (b["osm_id"],)).fetchone()
        hist = json.loads(row[0]) if row and row[0] else []
        if not b.get("_cached"):  # data dipakai ulang = bukan snapshot baru
            hist.append({"ts": now, "followers": b["followers"], "er": b["engagement_rate"]})
        # snapshot 0 = scan gagal, bukan followers asli -> jangan jadi titik awal delta
        real = [h["followers"] for h in hist if h.get("followers")]
        b["followers_delta"] = real[-1] - real[0] if len(real) > 1 else 0
        b.pop("_cached", None)
        conn.execute("""INSERT INTO businesses VALUES (?,?,?,?,?)
            ON CONFLICT(osm_id) DO UPDATE SET location=excluded.location, data=excluded.data,
            last_scanned=excluded.last_scanned, history=excluded.history""",
                     (b["osm_id"], location, json.dumps(b, ensure_ascii=False), now,
                      json.dumps(hist[-60:])))
    conn.commit()
    conn.close()

    os.makedirs(out_dir, exist_ok=True)
    fields = ["name", "brand", "category", "address", "instagram", "followers", "followers_delta",
              "engagement_rate", "avg_likes", "media_count", "grade", "website", "phone",
              "emails", "opening_hours", "lat", "lon", "osm_id"]
    with open(f"{out_dir}/competitors.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for b in sorted(biz, key=lambda b: -b["followers"]):
            w.writerow({**b, "emails": "|".join(b.get("emails", []))})
    log.info(f"[7/7] Tersimpan: ig.db (tabel businesses) + {out_dir}/competitors.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--location", required=True, help='mis. "Kemang, Jakarta Selatan"')
    ap.add_argument("--category", default="cafe", help=f"{', '.join(CATEGORIES)} atau key=value")
    ap.add_argument("--radius", type=int, default=2000, help="meter")
    ap.add_argument("--max", type=int, default=50, help="maks bisnis yang diproses")
    ap.add_argument("--ig-scrape", type=int, default=3, help="jumlah akun IG teratas yang di-scrape langsung")
    ap.add_argument("--mode", default="direct", help="mode ig_scraper.py")
    ap.add_argument("--no-trends", action="store_true")
    ap.add_argument("--refresh-hours", type=int, default=24,
                    help="bisnis yang dipindai lebih baru dari ini tidak di-scrape ulang (0 = paksa ulang)")
    args = ap.parse_args()

    lat, lon = geocode(args.location)
    time.sleep(1)  # kebijakan Nominatim: maks 1 request/detik
    biz = find_businesses(lat, lon, args.category, args.radius)[:args.max]
    if not biz:
        print("Tidak ada bisnis ditemukan. Coba radius lebih besar atau kategori lain.")
        return
    for b in biz:
        b.setdefault("emails", []); b.setdefault("prices", [])
    fresh = load_fresh(biz, args.refresh_hours)
    todo = [b for b in biz if b["osm_id"] not in fresh]
    for b in biz:
        if b["osm_id"] in fresh:
            b.update(fresh[b["osm_id"]])
            b["_cached"] = True
    log.info(f"      {len(fresh)} bisnis masih fresh (< {args.refresh_hours} jam) dipakai ulang, "
             f"{len(todo)} diproses")
    if todo:
        enrich_websites(todo)
        enrich_search(todo, args.location)
        enrich_socialblade(todo)
        merge_ig_db(todo)
    scrape_ig(biz, args.ig_scrape, args.mode)  # ig_scraper.py sendiri skip akun < 24 jam
    trend = {} if args.no_trends else trends(biz)
    save(biz, trend, args.location)

    print("\n=== KOMPETITOR (urut followers IG) ===")
    for b in sorted(biz, key=lambda b: -b["followers"])[:20]:
        ig = f"@{b['instagram']}" if b["instagram"] else "-"
        print(f"{b['name'][:28]:<28} {ig:<24} followers={b['followers']:<9} "
              f"ER={b['engagement_rate']}% grade={b['grade'] or '-'}")
    with_ig = sum(1 for b in biz if b["instagram"])
    print(f"\n{len(biz)} bisnis, {with_ig} dengan akun IG.")


if __name__ == "__main__":
    main()
