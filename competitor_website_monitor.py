"""
Monitor website kompetitor: harga, blog, link IG, email.
Jalan harian via cron. Output: diff vs run sebelumnya.

Daftar kompetitor di competitors.json:
    {"kompetitor_a": "https://kompetitor-a.com", ...}

Usage:
    python competitor_website_monitor.py
    python competitor_website_monitor.py --add nama https://domain.com
"""
import argparse
import hashlib
import json
import os
import re
import time

from bs4 import BeautifulSoup
import netutil  # curl_cffi + DNS-over-HTTPS (tahan blokir DNS ISP, SSL modern)

COMPETITORS_FILE = "competitors.json"
STATE_FILE = "website_state.json"
IG_RE = re.compile(r'instagram\.com/([A-Za-z0-9_.]+)')
IG_SKIP = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about", "legal"}
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[A-Za-z]{2,}(?:\.[A-Za-z]{2,})?')
EMAIL_SKIP = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "sentry", "example.",
              "domain.com", "email.com", "yourdomain", "@sentry")


def load_competitors():
    if os.path.exists(COMPETITORS_FILE):
        with open(COMPETITORS_FILE) as f:
            return json.load(f)
    return {}


def fetch(url):
    return netutil.get(url, timeout=15, retries=2)


def scan(url):
    try:
        r = fetch(url)
        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        emails = {e for e in EMAIL_RE.findall(html)
                  if not any(s in e.lower() for s in EMAIL_SKIP)}
        # cek halaman kontak juga
        base = url.rstrip("/")
        for path in ("/contact", "/kontak", "/contact-us", "/hubungi-kami"):
            try:
                rr = fetch(base + path)
                if rr.status_code == 200:
                    emails |= {e for e in EMAIL_RE.findall(rr.text)
                               if not any(s in e.lower() for s in EMAIL_SKIP)}
            except Exception:
                pass
        return {
            "status": r.status_code,
            "hash": hashlib.md5(r.content).hexdigest(),
            "ig_links": sorted({u for u in IG_RE.findall(html) if u.lower() not in IG_SKIP}),
            "emails": sorted(emails),
            "title": (soup.title.string or "").strip() if soup.title else "",
            "h1": [h.get_text(strip=True) for h in soup.find_all("h1")][:3],
            "prices": re.findall(r'Rp\s?[\d.,]+', html)[:10],
            "blog_links": sorted(set(re.findall(
                r'href="(/blog/[^"]+|/artikel/[^"]+)"', html)))[:10],
            "scanned_at": time.strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        return {"error": str(e), "scanned_at": time.strftime("%Y-%m-%d %H:%M")}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", nargs=2, metavar=("NAMA", "URL"), help="tambah kompetitor")
    args = ap.parse_args()

    comps = load_competitors()
    if args.add:
        comps[args.add[0]] = args.add[1]
        with open(COMPETITORS_FILE, "w") as f:
            json.dump(comps, f, indent=2)
        print(f"Ditambahkan: {args.add[0]} -> {args.add[1]}")
        return
    if not comps:
        print(f"{COMPETITORS_FILE} kosong. Tambah dulu: "
              f"python competitor_website_monitor.py --add nama https://domain.com")
        return

    old = load_state()
    new = {}
    for name, url in comps.items():
        print(f"[scan] {name} ({url})")
        data = scan(url)
        new[name] = data
        if "error" in data:
            print(f"  error: {data['error'][:100]}")
        else:
            print(f"  IG={data['ig_links']} email={data['emails'][:3]} harga={data['prices'][:3]}")
        prev = old.get(name, {})
        if prev.get("hash") and prev["hash"] != data.get("hash"):
            print(f"  ⚠️ {name} berubah!")
            if prev.get("prices") != data.get("prices"):
                print(f"  harga: {prev.get('prices')} → {data.get('prices')}")
            added = set(data.get("ig_links", [])) - set(prev.get("ig_links", []))
            if added:
                print(f"  IG baru: {added}")
        time.sleep(2)
    save_state(new)


if __name__ == "__main__":
    main()
