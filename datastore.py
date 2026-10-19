"""
datastore.py, lapisan pembersih data sebelum disimpan.

Semua hasil scraping lewat sini dulu:
  normalize   : HP -> 62xxx, website tanpa tracking, IG valid, teks tanpa ikon/spasi aneh
  validate    : rating 1-5, review >= 0, buang yang tutup permanen
  filter_area : buang hasil yang nyasar jauh dari wilayah
  dedupe      : gabungkan bisnis yang sama (place_id / HP / website / IG / nama mirip + lokasi dekat)
  flag_shared : tandai akun IG / website yang dipakai banyak cabang (jangan dihitung berkali-kali)
  snapshot    : simpan riwayat hanya kalau angkanya berubah
"""
import math
import re
import statistics
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

PUA_RE = re.compile(r"[-​-‏‪-‮﻿]")
IG_VALID = re.compile(r"^[a-z0-9._]{3,30}$")
IG_RESERVED = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about", "legal",
               "direct", "popular", "instagram", "share", "developer"}
TRACKING = re.compile(r"^(utm_|fbclid|gclid|igshid|ref$|si$|mc_)")
LINK_PAGES = ("linktr.ee", "heylink.me", "linkin.bio", "bio.link", "lynk.id", "msha.ke",
              "beacons.ai", "s.id", "wa.me", "api.whatsapp.com")
CLOSED = ("tutup permanen", "permanently closed", "tutup sementara", "temporarily closed")
NAME_NOISE = re.compile(r"\b(cabang|branch|outlet|store|official|indonesia|jakarta|bandung|"
                        r"surabaya|tangerang|bekasi|depok|bogor|kota|kab\.?)\b", re.I)


# ------------------------------------------------------------
# normalize
# ------------------------------------------------------------

def clean_text(s):
    if not s:
        return ""
    s = PUA_RE.sub("", str(s))
    s = re.sub(r"\s+", " ", s).strip(" ·,-|")
    return s


def norm_phone(s):
    """'0813-1891 1529' / '+62 813...' -> '6281318911529'. Kosong kalau tidak valid."""
    d = re.sub(r"\D", "", s or "")
    if d.startswith("0"):
        d = "62" + d[1:]
    elif d.startswith("8") and 9 <= len(d) <= 13:
        d = "62" + d
    return d if d.startswith("62") and 9 <= len(d) <= 15 else ""


def is_mobile(phone):
    return phone.startswith("628")


def norm_url(u):
    """Buang tracking param, fragment, trailing slash; host lowercase. Link-in-bio dipertahankan."""
    u = (u or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        p = urlparse(u)
    except ValueError:
        return ""
    host = re.sub(r"^www\.", "", p.netloc.lower())
    if not host or "." not in host:
        return ""
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if not TRACKING.match(k.lower())])
    return urlunparse(("https", host, p.path.rstrip("/"), "", q, ""))


def domain(u):
    try:
        return urlparse(u).netloc.lower()
    except ValueError:
        return ""


def is_link_page(u):
    d = domain(u)
    return any(d == x or d.endswith("." + x) for x in LINK_PAGES)


def norm_ig(h):
    """'@Kopi.Kenangan/' / 'instagram.com/x?igshid=..' -> 'kopi.kenangan'. Kosong kalau tidak valid."""
    h = (h or "").strip()
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", h)
    h = (m.group(1) if m else h).lstrip("@").rstrip("/").lower()
    if not IG_VALID.match(h) or h in IG_RESERVED or len(h.strip("._")) < 3 or ".." in h:
        return ""
    return h


def name_key(name):
    """Kunci pembanding nama: huruf kecil, tanpa tanda baca & kata generik lokasi/cabang."""
    n = clean_text(name).lower()
    n = re.split(r"\s[-|@·(]\s?", n)[0]
    n = NAME_NOISE.sub(" ", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


# ------------------------------------------------------------
# validate + normalize satu record
# ------------------------------------------------------------

def clean_place(p):
    """Return record bersih, atau None kalau tidak layak disimpan."""
    p = dict(p)
    p["name"] = clean_text(p.get("name"))
    if len(p["name"]) < 2:
        return None
    for k in ("category", "address", "status", "price"):
        if k in p:
            p[k] = clean_text(p.get(k))
    if re.match(r"^(buka|tutup|open|closed)\b", (p.get("category") or "").lower()):
        p["category"] = ""  # status jam yang salah terbaca sebagai kategori
    if any(c in (p.get("status") or "").lower() for c in CLOSED):
        return None
    r = p.get("rating")
    p["rating"] = round(float(r), 1) if isinstance(r, (int, float)) and 1 <= r <= 5 else None
    try:
        p["reviews"] = max(int(p.get("reviews") or 0), 0)
    except (TypeError, ValueError):
        p["reviews"] = 0
    if p["rating"] is None:
        p["reviews"] = 0  # review tanpa rating = data parsing rusak
    if p.get("phone"):
        p["phone"] = norm_phone(p["phone"])
    if p.get("website"):
        w = norm_url(p["website"])
        if "instagram.com/" in w and not p.get("instagram"):
            p["instagram"] = w
            w = ""
        p["website"] = w
    if p.get("instagram"):
        p["instagram"] = norm_ig(p["instagram"])
    if p.get("emails"):
        p["emails"] = sorted({e.lower() for e in p["emails"]
                              if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", e)
                              and not e.lower().endswith((".png", ".jpg", ".webp"))})[:5]
    for k in ("lat", "lon"):
        if p.get(k) is not None and not isinstance(p[k], (int, float)):
            p.pop(k)
    p["name_key"] = name_key(p["name"])
    return p


# ------------------------------------------------------------
# filter lokasi
# ------------------------------------------------------------

def haversine_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def filter_area(places, max_km=None):
    """Buang tempat yang jauh dari pusat hasil (median koordinat). Return (kept, dropped)."""
    pts = [(p["lat"], p["lon"]) for p in places if p.get("lat") is not None and p.get("lon") is not None]
    if len(pts) < 5:
        return places, []
    center = (statistics.median(x[0] for x in pts), statistics.median(x[1] for x in pts))
    dists = sorted(haversine_km(center, x) for x in pts)
    # batas otomatis: 2.5x jarak median, minimal 3 km, maksimal 15 km
    limit = max_km or min(max(dists[len(dists) // 2] * 2.5, 3.0), 15.0)
    kept, dropped = [], []
    for p in places:
        if p.get("lat") is None:
            kept.append(p)
            continue
        d = haversine_km(center, (p["lat"], p["lon"]))
        p["dist_km"] = round(d, 2)
        (kept if d <= limit else dropped).append(p)
    return kept, dropped


# ------------------------------------------------------------
# dedupe
# ------------------------------------------------------------

def _same(a, b):
    if a.get("place_id") and a.get("place_id") == b.get("place_id"):
        return True
    if a.get("phone") and a["phone"] == b.get("phone") and is_mobile(a["phone"]):
        # HP sama + nama mirip = bisnis sama (HP pemilik kadang dipakai beberapa usaha)
        return _name_sim(a, b) >= 70
    near = _near(a, b, 0.15)
    if near is not None and not near:
        return False
    if a.get("website") and a["website"] == b.get("website") and not is_link_page(a["website"]):
        return near is not False and _name_sim(a, b) >= 60
    return near is True and _name_sim(a, b) >= 88


def _name_sim(a, b):
    ka, kb = a.get("name_key", ""), b.get("name_key", "")
    if not ka or not kb:
        return 0
    if fuzz:
        return fuzz.token_set_ratio(ka, kb)
    return 100 if ka == kb else 0


def _near(a, b, km):
    if a.get("lat") is None or b.get("lat") is None:
        return None
    return haversine_km((a["lat"], a["lon"]), (b["lat"], b["lon"])) <= km


def _merge(dst, src):
    for k, v in src.items():
        if v in (None, "", [], 0) or k in ("history",):
            continue
        if k == "reviews":
            dst[k] = max(dst.get(k) or 0, v)
        elif k == "emails":
            dst[k] = sorted(set(dst.get(k) or []) | set(v))[:5]
        elif not dst.get(k):
            dst[k] = v
    dst.setdefault("merged_ids", []).append(src.get("place_id"))


def dedupe(places):
    """Return (unik, jumlah_digabung)."""
    out = []
    merged = 0
    for p in sorted(places, key=lambda x: -(x.get("reviews") or 0)):
        twin = next((q for q in out if _same(q, p)), None)
        if twin:
            _merge(twin, p)
            merged += 1
        else:
            out.append(p)
    return out, merged


def flag_shared(places):
    """Akun IG / website yang muncul di >1 tempat = akun brand/cabang, bukan milik outlet itu."""
    for key in ("instagram", "website"):
        cnt = {}
        for p in places:
            v = p.get(key)
            if v and not (key == "website" and is_link_page(v)):
                cnt[v] = cnt.get(v, 0) + 1
        for p in places:
            v = p.get(key)
            p[f"{key}_shared"] = bool(v and cnt.get(v, 0) > 1)
    # cabang: tempat yang berbagi akun IG / website (bukan link-in-bio) / nama inti yang sama
    parent = list(range(len(places)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, p in enumerate(places):
        keys = [("n", p.get("name_key"))]
        if p.get("instagram"):
            keys.append(("ig", p["instagram"]))
        if p.get("website") and not is_link_page(p["website"]):
            keys.append(("w", p["website"]))
        for k in keys:
            if not k[1]:
                continue
            if k in seen:
                parent[find(i)] = find(seen[k])
            else:
                seen[k] = i
    size = {}
    for i in range(len(places)):
        size[find(i)] = size.get(find(i), 0) + 1
    for i, p in enumerate(places):
        p["branches_in_area"] = size[find(i)]
        p["brand_group"] = find(i)
    return places


def clean_batch(places, area_filter=True):
    """Pipeline lengkap. Return (bersih, stats)."""
    stats = {"raw": len(places)}
    cleaned = [c for c in (clean_place(p) for p in places) if c]
    stats["invalid_or_closed"] = len(places) - len(cleaned)
    dropped = []
    if area_filter:
        cleaned, dropped = filter_area(cleaned)
    stats["out_of_area"] = len(dropped)
    uniq, merged = dedupe(cleaned)
    stats["duplicates_merged"] = merged
    flag_shared(uniq)
    stats["kept"] = len(uniq)
    stats["chains"] = len({p["brand_group"] for p in uniq if p.get("branches_in_area", 1) > 1})
    stats["brands"] = len({p["brand_group"] for p in uniq})
    return uniq, stats


# ------------------------------------------------------------
# relevansi: buang bisnis yang bukan industri yang dicari
# ------------------------------------------------------------

# kata kunci kategori Google Maps (ID/EN) per industri; dipakai kalau AI tidak tersedia
INDUSTRY_WORDS = {
    "agency": ["agen pemasaran", "pemasaran", "marketing", "biro iklan", "periklanan", "advertising", "agensi",
               "agency", "desain", "design", "perancang situs", "web", "media", "kreatif", "creative",
               "branding", "humas", "public relations", "produksi video", "seo"],
    "coffee": ["kopi", "coffee", "kafe", "cafe", "café", "kedai", "espresso", "roaster", "penyangraian"],
    "salon": ["salon", "kecantikan", "beauty", "rambut", "hair", "barber", "cukur", "spa", "nail", "kuku"],
    "restaurant": ["restoran", "restaurant", "rumah makan", "warung makan", "masakan", "makanan", "bistro", "kuliner"],
    "gym": ["gym", "fitness", "kebugaran", "pusat olahraga", "yoga", "pilates", "crossfit"],
    "laundry": ["laundry", "binatu", "dry clean", "cuci"],
    "bakery": ["roti", "bakery", "kue", "pastry", "cake"],
    "clinic": ["klinik", "clinic", "dokter", "medis", "kesehatan"],
    "dentist": ["gigi", "dental", "dentist", "ortodont"],
    "printing": ["percetakan", "cetak", "fotokopi", "print", "sablon", "digital printing"],
    "property": ["properti", "realestat", "real estate", "agen properti", "developer perumahan"],
    "wedding": ["pernikahan", "wedding", "event organizer", "dekorasi", "katering"],
    "catering": ["katering", "catering", "makanan"],
    "software": ["perangkat lunak", "software", "pengembang", "aplikasi", "it", "teknologi", "web"],
}
INDUSTRY_ALIASES = {
    "agency": ["agency", "agensi", "agen digital", "creative", "kreatif", "marketing", "advertising", "branding"],
    "coffee": ["coffee", "kopi", "cafe", "kafe", "café"],
    "salon": ["salon", "barber", "kecantikan", "beauty"],
    "restaurant": ["resto", "restoran", "restaurant", "rumah makan"],
    "gym": ["gym", "fitness"],
    "laundry": ["laundry"],
    "bakery": ["bakery", "roti", "kue"],
    "clinic": ["klinik", "clinic"],
    "dentist": ["gigi", "dental", "dentist"],
    "printing": ["percetakan", "printing", "fotokopi", "fotocopy"],
    "property": ["properti", "property", "real estate"],
    "wedding": ["wedding", "pernikahan", "wo"],
    "catering": ["katering", "catering"],
    "software": ["software", "it ", "aplikasi", "developer"],
}


def industry_group(industry):
    t = " " + industry.lower() + " "
    for g, aliases in INDUSTRY_ALIASES.items():
        if any(a in t for a in aliases):
            return g
    return None


def industry_tokens(industry):
    stop = {"shop", "toko", "jasa", "di", "dan", "the", "&", "services", "service"}
    stop |= {"digital", "online", "jasa", "layanan", "pusat", "center", "studio"}  # terlalu generik
    return [w for w in re.findall(r"[a-z0-9]+", industry.lower()) if w not in stop and len(w) > 2]


def rule_verdicts(industry, categories):
    """{kategori: 'yes'|'no'|'maybe'} dari kata kunci."""
    g = industry_group(industry)
    words = INDUSTRY_WORDS.get(g, []) + industry_tokens(industry)
    out = {}
    for c in categories:
        cl = (c or "").lower()
        if not cl:
            out[c] = "maybe"
        elif any(w in cl for w in words):
            out[c] = "yes"
        else:
            out[c] = "no" if g else "maybe"  # industri tak dikenal: jangan buang sembarangan
    return out


def apply_relevance(places, industry, verdicts):
    """Return (relevan, dibuang). 'maybe' diterima hanya kalau nama mengandung kata kunci industri."""
    g = industry_group(industry)
    name_words = [w.strip() for w in INDUSTRY_ALIASES.get(g, [])] + industry_tokens(industry)
    kept, dropped = [], []
    for p in places:
        v = verdicts.get(p.get("category") or "", "maybe")
        nm = (p.get("name") or "").lower()
        ok = v == "yes" or (v == "maybe" and (any(w and w in nm for w in name_words) or not p.get("category")))
        p["relevance"] = v
        (kept if ok else dropped).append(p)
    # pengaman: kalau hampir semua terbuang, filternya yang salah, bukan datanya
    if len(places) >= 10 and len(kept) < 0.25 * len(places):
        for p in dropped:
            p["relevance"] = "unverified"
        return places, []
    return kept, dropped


# ------------------------------------------------------------
# riwayat
# ------------------------------------------------------------

def snapshot(history, values, min_hours=6):
    """Tambah snapshot hanya kalau ada angka yang berubah, atau snapshot terakhir sudah > 7 hari
    (supaya tetap ada titik data rutin). Scan berulang dalam `min_hours` tidak menambah titik."""
    history = list(history or [])
    now = datetime.now(timezone.utc)
    vals = {k: v for k, v in values.items() if v not in (None, "")}
    if not vals or all(not v for v in vals.values()):
        return history  # data kosong/gagal tidak dicatat
    if history:
        last = history[-1]
        try:
            age_h = (now - datetime.fromisoformat(last["ts"])).total_seconds() / 3600
        except (KeyError, ValueError):
            age_h = 1e9
        changed = any(last.get(k) != v for k, v in vals.items())
        if age_h < min_hours or (not changed and age_h < 24 * 7):
            return history
    history.append({"ts": now.isoformat(), **vals})
    return history[-60:]
