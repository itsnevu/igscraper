"""
report_dashboard.py
===================
Generate HTML report dari ig.db, tanpa dependency eksternal.

Usage:
    python report_dashboard.py --db ig.db --out report.html
"""

import argparse
import html
import json
import os
import sqlite3
from datetime import datetime


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def fmt_num(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def svg_bar(values: list, width: int = 300, height: int = 60,
            color: str = "#4f46e5") -> str:
    if not values:
        return ""
    mx = max(values) or 1
    bw = width / max(len(values), 1)
    bars = []
    for i, v in enumerate(values):
        h = (v / mx) * (height - 10)
        bars.append(f'<rect x="{i*bw:.1f}" y="{height-h-5:.1f}" width="{bw*0.8:.1f}" '
                    f'height="{h:.1f}" fill="{color}" rx="2"/>')
    return f'<svg width="{width}" height="{height}">{"".join(bars)}</svg>'


def svg_line(values: list, width: int = 300, height: int = 60,
             color: str = "#10b981") -> str:
    if len(values) < 2:
        return svg_bar(values, width, height, color)
    mx, mn = max(values), min(values)
    rng = (mx - mn) or 1
    n = len(values)
    pts = " ".join(
        f"{(i/(n-1))*(width-10)+5:.1f},{height-((v-mn)/rng)*(height-10)-5:.1f}"
        for i, v in enumerate(values))
    return (f'<svg width="{width}" height="{height}">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/></svg>')


def load_profiles(db_path: str) -> list:
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        out = []
        for uname, data_json, followers, hist_json in conn.execute(
                "SELECT username, data, followers, followers_history FROM profiles"):
            try:
                d = json.loads(data_json) if data_json else {}
            except json.JSONDecodeError:
                d = {}
            try:
                hist = json.loads(hist_json) if hist_json else []
            except json.JSONDecodeError:
                hist = []
            d["_username"] = uname
            d["_followers"] = followers or d.get("followers", 0)
            d["_history"] = hist
            out.append(d)
        return out
    finally:
        conn.close()


CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
       sans-serif; background: #f8fafc; color: #1e293b; padding: 24px; }
h1 { font-size: 24px; margin-bottom: 4px; }
h2 { font-size: 18px; margin: 32px 0 16px; color: #334155; }
.sub { color: #64748b; font-size: 13px; margin-bottom: 24px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
         gap: 12px; margin-bottom: 32px; }
.card { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }
.card .label { font-size: 11px; text-transform: uppercase; color: #64748b; letter-spacing: 0.5px; }
.card .value { font-size: 26px; font-weight: 700; margin-top: 4px; }
.card .sub { font-size: 12px; color: #94a3b8; margin: 0; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; background: #fff;
        border: 1px solid #e2e8f0; font-size: 13px; }
th { background: #f1f5f9; text-align: left; padding: 10px 12px; font-weight: 600;
     color: #475569; font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px;
     cursor: pointer; }
th:hover { background: #e2e8f0; }
td { padding: 10px 12px; border-top: 1px solid #f1f5f9; }
tr:hover td { background: #f8fafc; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.b2b { background: #dbeafe; color: #1e40af; }
.company { background: #dcfce7; color: #166534; }
.kol { background: #fef3c7; color: #92400e; }
.personal { background: #f1f5f9; color: #475569; }
.unknown { background: #f3e8ff; color: #6b21a8; }
a { color: #4f46e5; text-decoration: none; }
a:hover { text-decoration: underline; }
.er-high { color: #059669; font-weight: 600; }
.er-mid { color: #d97706; font-weight: 600; }
.er-low { color: #dc2626; font-weight: 600; }
details { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
          padding: 12px 16px; margin-bottom: 8px; }
summary { cursor: pointer; font-weight: 600; }
.detail-body { margin-top: 12px; font-size: 13px; }
.stat-row { display: flex; gap: 24px; flex-wrap: wrap; margin: 12px 0; }
.stat { font-size: 12px; color: #64748b; }
.stat strong { color: #1e293b; font-size: 14px; display: block; }
.filter-bar { margin-bottom: 16px; }
.filter-bar button { padding: 6px 14px; border: 1px solid #e2e8f0; background: #fff;
                     border-radius: 6px; cursor: pointer; margin: 0 6px 6px 0; font-size: 13px; }
.filter-bar button.active { background: #4f46e5; color: #fff; border-color: #4f46e5; }
"""

JS = """
function filterType(btn, t) {
  document.querySelectorAll('.filter-bar button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('tbody tr').forEach(tr => {
    tr.style.display = (t === 'ALL' || tr.dataset.type === t) ? '' : 'none';
  });
}
function sortTable(th) {
  const tbody = th.closest('table').querySelector('tbody');
  const idx = Array.from(th.parentNode.children).indexOf(th);
  const asc = !th.classList.contains('asc');
  th.parentNode.querySelectorAll('th').forEach(h => h.classList.remove('asc','desc'));
  th.classList.add(asc ? 'asc' : 'desc');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {
    const av = a.children[idx].dataset.v || a.children[idx].textContent;
    const bv = b.children[idx].dataset.v || b.children[idx].textContent;
    const an = parseFloat(av), bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return asc ? an-bn : bn-an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach(r => tbody.appendChild(r));
}
"""


def badge(ptype: str) -> str:
    cls = {"B2B": "b2b", "COMPANY": "company", "KOL": "kol",
           "PERSONAL": "personal"}.get(ptype, "unknown")
    return f'<span class="badge {cls}">{esc(ptype)}</span>'


def er_class(er: float) -> str:
    if er >= 3: return "er-high"
    if er >= 1: return "er-mid"
    return "er-low"


def build_summary(profiles: list) -> str:
    by_type = {}
    for p in profiles:
        t = p.get("profile_type", "UNKNOWN")
        by_type[t] = by_type.get(t, 0) + 1
    ers = [p.get("stats", {}).get("engagement_rate", 0) for p in profiles
           if p.get("stats", {}).get("engagement_rate")]
    avg_er = sum(ers) / len(ers) if ers else 0
    cards = [
        ("Total Profil", len(profiles), ""),
        ("Total Followers", fmt_num(sum(p.get("_followers", 0) for p in profiles)), "gabungan"),
        ("Avg Engagement Rate", f"{avg_er:.2f}%", "sample"),
        ("B2B", by_type.get("B2B", 0), ""),
        ("Company", by_type.get("COMPANY", 0), ""),
        ("KOL", by_type.get("KOL", 0), ""),
    ]
    return '<div class="cards">' + "".join(
        f'<div class="card"><div class="label">{esc(l)}</div>'
        f'<div class="value">{esc(v)}</div><div class="sub">{esc(s)}</div></div>'
        for l, v, s in cards) + '</div>'


def build_leaderboard(profiles: list) -> str:
    rows = []
    for p in profiles:
        uname = p.get("_username", "")
        ptype = p.get("profile_type", "UNKNOWN")
        stats = p.get("stats", {})
        er = stats.get("engagement_rate", 0) or 0
        followers = p.get("_followers", 0) or 0
        score = p.get("score", 0) or 0
        posts = p.get("posts_count", 0) or 0
        avg_l = stats.get("avg_likes", 0) or 0
        avg_c = stats.get("avg_comments", 0) or 0
        reasons = "|".join(p.get("reasons", [])[:4])
        rows.append(f"""
        <tr data-type="{esc(ptype)}">
          <td><a href="https://instagram.com/{esc(uname)}" target="_blank">@{esc(uname)}</a></td>
          <td>{badge(ptype)}</td>
          <td data-v="{followers}">{fmt_num(followers)}</td>
          <td data-v="{er}" class="{er_class(er)}">{er:.2f}%</td>
          <td data-v="{score}">{score}</td>
          <td data-v="{posts}">{fmt_num(posts)}</td>
          <td data-v="{avg_l}">{fmt_num(avg_l)}</td>
          <td data-v="{avg_c}">{fmt_num(avg_c)}</td>
          <td style="font-size:11px;color:#94a3b8">{esc(reasons[:60])}</td>
        </tr>""")
    active = ' class="active"'
    buttons = "".join(
        f'<button{active if t == "ALL" else ""} '
        f'onclick="filterType(this, \'{t}\')">{label}</button>'
        for t, label in [("ALL", "Semua"), ("B2B", "B2B"), ("COMPANY", "Company"),
                         ("KOL", "KOL"), ("PERSONAL", "Personal")])
    heads = "".join(f'<th onclick="sortTable(this)">{h}</th>' for h in
                    ["Username", "Tipe", "Followers", "ER", "Score", "Posts",
                     "Avg Likes", "Avg Comments"])
    return f"""
    <div class="filter-bar">{buttons}</div>
    <div class="table-wrap"><table>
      <thead><tr>{heads}<th>Reasons</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>"""


def build_growth_section(profiles: list) -> str:
    items = []
    for p in profiles:
        hist = p.get("_history", [])
        if len(hist) < 2:
            continue
        values = [h.get("followers", 0) for h in hist]
        delta = values[-1] - values[0]
        pct = (delta / values[0] * 100) if values[0] else 0
        sign = "+" if delta >= 0 else ""
        color = "#059669" if delta >= 0 else "#dc2626"
        items.append(f"""
        <details>
          <summary>@{esc(p.get("_username", ""))}, <span style="color:{color}">
            {sign}{fmt_num(delta)} ({sign}{pct:.2f}%)</span>
            <span style="float:right;font-weight:400;color:#94a3b8;font-size:12px">
            {len(hist)} snapshot</span></summary>
          <div class="detail-body">
            {svg_line(values, width=280, height=50)}
            <div class="stat-row">
              <div class="stat"><strong>{fmt_num(values[0])}</strong>awal</div>
              <div class="stat"><strong>{fmt_num(values[-1])}</strong>akhir</div>
              <div class="stat"><strong>{sign}{fmt_num(delta)}</strong>delta</div>
            </div>
          </div>
        </details>""")
    return "".join(items) or ('<p style="color:#94a3b8">Belum ada data growth. '
                              'Jalankan scrape minimal 2x untuk melihat tren.</p>')


def build_media_mix(profiles: list) -> str:
    agg = {"image": 0, "video": 0, "reel": 0, "carousel": 0}
    for p in profiles:
        for k, v in (p.get("stats", {}).get("media_mix", {}) or {}).items():
            agg[k] = agg.get(k, 0) + v
    total = sum(agg.values()) or 1
    bars = []
    for k, v in agg.items():
        pct = v / total * 100
        if pct < 1:
            continue
        bars.append(f"""
        <div style="margin-bottom:8px">
          <div style="font-size:12px;color:#475569;margin-bottom:2px">{esc(k)}, {v} post ({pct:.1f}%)</div>
          <div style="background:#e2e8f0;border-radius:4px;height:8px;overflow:hidden">
            <div style="background:#4f46e5;width:{pct}%;height:100%"></div></div>
        </div>""")
    return "".join(bars) or "<p style='color:#94a3b8'>Tidak ada data media.</p>"


def build_top_posts(profiles: list) -> str:
    items = []
    for p in profiles:
        top = p.get("stats", {}).get("top_posts", [])[:3]
        if not top:
            continue
        li = "".join(
            f'<li><a href="{esc(t.get("url", ""))}" target="_blank">'
            f'{fmt_num(t.get("likes", 0))} likes · {fmt_num(t.get("comments", 0))} komentar</a></li>'
            for t in top)
        items.append(f'<details><summary>@{esc(p.get("_username", ""))}</summary>'
                     f'<div class="detail-body"><ul style="padding-left:18px">{li}</ul></div></details>')
    return "".join(items) or "<p style='color:#94a3b8'>Tidak ada data post.</p>"


def build_html(profiles: list, title: str = "IG Scraper Report") -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
  <h1>{esc(title)}</h1>
  <div class="sub">Dibuat {ts} · {len(profiles)} profil · data publik</div>
  {build_summary(profiles)}
  <h2>Leaderboard</h2>
  {build_leaderboard(profiles)}
  <h2>Media Mix (agregat)</h2>
  {build_media_mix(profiles)}
  <h2>Top Posts</h2>
  {build_top_posts(profiles)}
  <h2>Growth Tracking</h2>
  {build_growth_section(profiles)}
<script>{JS}</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="ig.db")
    ap.add_argument("--out", default="report.html")
    ap.add_argument("--title", default="IG Scraper Report")
    args = ap.parse_args()

    profiles = load_profiles(args.db)
    if not profiles:
        print(f"Tidak ada data di {args.db}. Jalankan scrape dulu.")
        return
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(profiles, args.title))
    print(f"Report: {args.out} ({len(profiles)} profil, "
          f"{os.path.getsize(args.out) // 1024}KB)")
    print(f"  Buka: open {args.out}")


if __name__ == "__main__":
    main()
