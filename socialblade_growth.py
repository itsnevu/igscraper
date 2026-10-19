"""
Statistik akun IG dari halaman publik Social Blade.

Catatan: Social Blade sekarang pakai Next.js, data ada di JSON __NEXT_DATA__,
bukan tabel HTML. Riwayat followers ('instagram.history') kosong di halaman
publik, jadi growth tetap dicatat sendiri lewat ig.db (scrape berkala).

Usage:
    python socialblade_growth.py nike adidas
"""
import json
import re
import sys

import netutil

NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def growth(username):
    url = f"https://socialblade.com/instagram/user/{username}"
    r = netutil.get(url)
    if r.status_code != 200:
        print(f"  {username}: HTTP {r.status_code}")
        return None
    m = NEXT_RE.search(r.text)
    if not m:
        return None
    queries = (json.loads(m.group(1)).get("props", {}).get("pageProps", {})
               .get("trpcState", {}).get("json", {}).get("queries", []))
    out = {}
    for q in queries:
        key, data = q.get("queryKey", [None])[0], q.get("state", {}).get("data")
        if key == ["instagram", "user"] and data:
            s = data.get("stats") or {}
            out.update({
                "username": data.get("username"),
                "followers": int(s.get("followers") or 0),
                "following": s.get("following"),
                "media_count": s.get("mediaCount"),
                "engagement_rate": round(s.get("engagementRate") or 0, 3),  # sudah dalam %
                "avg_likes": round(s.get("averageLikes") or 0, 1),
                "avg_comments": round(s.get("averageComments") or 0, 1),
                "grade": data.get("grade"),
                "rank_sb": (data.get("ranks") or {}).get("sb"),
                "rank_followers": (data.get("ranks") or {}).get("followers"),
            })
        elif key == ["instagram", "history"] and data:
            out["history"] = data
    return out or None


if __name__ == "__main__":
    for u in sys.argv[1:] or ["nike"]:
        d = growth(u)
        print(json.dumps(d, indent=2, ensure_ascii=False) if d else f"{u}: tidak ada data")
