#!/usr/bin/env python3
"""
ig_superscraper.py
==================
Advanced Instagram public-data scraper.

Fitur:
 1. Fallback chain: web_profile_info -> GraphQL -> Playwright
 2. Playwright network interceptor (capture JSON asli dari IG)
 3. Cookie persistence
 4. Proxy rotator per-detik + health check + blacklist
 5. Hashtag discovery
 6. Related-profiles discovery (chaining)
 7. Reels deep metrics (tab /reels/)
 8. Website email enrichment
 9. Stats: ER, avg, frequency, best day/hour, media mix
10. Comment intent mining
11. Async batch (httpx)
12. Structured logging + metrics
13. SQLite storage + growth history + diff mode
14. Entity resolution (dedupe multi-akun brand)
15. Scheduler + Telegram/Slack alert
16. Compliance: robots.txt, rate budget, PII flag, opt-out

Usage:
  python ig_superscraper.py --targets instagram,nike --max-posts 30 --out hasil
  python ig_superscraper.py --hashtags startupindonesia --discover-limit 30 --out hasil
  python ig_superscraper.py --schedule 6h --targets-file targets.txt --out hasil
  python ig_superscraper.py --report --out hasil
"""

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlparse

import requests

try:
    # TLS fingerprint browser asli; requests biasa dapat HTML yang data-nya dibuang
    from curl_cffi import requests as creq
except ImportError:
    creq = None

try:
    import httpx
except ImportError:
    httpx = None

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

try:
    # port Python dari apify/fingerprint-suite (Bayesian network + injector)
    from browserforge.fingerprints import FingerprintGenerator
    from browserforge.injectors.playwright import NewContext
except ImportError:
    FingerprintGenerator = None

from ig_modes import resolve_mode, runtime_guard


# ============================================================
# CONFIG
# ============================================================

# proxy_file, request_jitter, max_requests_per_hour di-override oleh --mode di main()
CFG = {
    "app_id": "936619743392459",
    "proxy_file": "proxies.txt",
    "proxy_rotate_seconds": 1.0,
    "proxy_health_every": 60,
    "request_jitter": (2.5, 5.5),
    "max_requests_per_hour": 200,
    "robots_check": True,
    "db_path": "ig.db",
    "state_file": "ig_state.json",
    # profil TLS/HTTP2 curl_cffi; dipilih acak per sesi, ganti saat 403 (ala Impit)
    "impersonate_pool": ["chrome136", "chrome133a", "chrome131"],
    "telegram_token": os.getenv("TG_TOKEN", ""),
    "telegram_chat": os.getenv("TG_CHAT", ""),
    "slack_webhook": os.getenv("SLACK_WEBHOOK", ""),
    "user_agents": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ig")
METRICS = Counter()
_GUARD_RECORD, _GUARD_MULT = runtime_guard()


# ============================================================
# 16. COMPLIANCE
# ============================================================

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(\+62|62|0)8[1-9][0-9]{6,10}")
OPT_OUT_FILE = "opt_out.txt"


class Compliance:
    def __init__(self):
        self.opt_out = self._load_opt_out()
        self._req_log = []
        self._lock = threading.Lock()

    def _load_opt_out(self) -> set:
        if os.path.exists(OPT_OUT_FILE):
            with open(OPT_OUT_FILE) as f:
                return {ln.strip().lower().lstrip("@") for ln in f if ln.strip()}
        return set()

    def is_opted_out(self, username: str) -> bool:
        return username.lower().lstrip("@") in self.opt_out

    def budget_ok(self) -> bool:
        while True:
            budget_mult, _ = _GUARD_MULT()
            budget = max(1, int(CFG["max_requests_per_hour"] * budget_mult))
            with self._lock:
                now = time.time()
                self._req_log = [t for t in self._req_log if now - t < 3600]
                if len(self._req_log) < budget:
                    self._req_log.append(now)
                    return True
            # tidur di luar lock (Lock tidak reentrant)
            log.warning(f"Rate budget per jam habis ({budget}/jam), tidur 60s")
            time.sleep(60)

    def check_robots(self, url: str) -> bool:
        if not CFG["robots_check"]:
            return True
        try:
            from urllib.robotparser import RobotFileParser
            p = urlparse(url)
            robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            return rp.can_fetch("*", url)
        except Exception:
            return True

    @staticmethod
    def pii_flags(profile_dict: dict) -> list:
        """Tandai field yang mungkin PII."""
        flags = []
        if profile_dict.get("business_phone"):
            flags.append("has_business_phone")
        if profile_dict.get("phones_in_bio"):
            flags.append("phone_in_bio")
        personal_domains = ("gmail", "yahoo", "hotmail", "outlook")
        for e in profile_dict.get("emails_in_bio", []) + [profile_dict.get("business_email", "")]:
            if e and any(d in e for d in personal_domains):
                flags.append("personal_email")
                break
        return flags


COMPLIANCE = Compliance()


# ============================================================
# 4. PROXY ROTATOR PER-DETIK
# ============================================================

class ProxyRotator:
    def __init__(self, proxy_file: str, rotate_seconds: float = 1.0):
        self.proxies = self._load(proxy_file)
        self.rotate_seconds = rotate_seconds
        self.blacklist: set = set()
        self._healthy: set = set(self.proxies)
        self._idx = 0
        self._current = None
        self._last_rotate = 0.0
        self._lock = threading.Lock()

        if self.proxies:
            threading.Thread(target=self._health_loop, daemon=True).start()
            log.info(f"[Proxy] {len(self.proxies)} loaded, rotate={rotate_seconds}s")

    def _load(self, path):
        if not path:
            return []
        if not os.path.exists(path):
            log.info(f"[Proxy] {path} tidak ada, jalan tanpa proxy")
            return []
        with open(path) as f:
            lines = [ln.strip() for ln in f
                     if ln.strip() and not ln.strip().startswith("#")]
        return [self._normalize(ln) for ln in lines]

    @staticmethod
    def _normalize(ln: str) -> str:
        """Terima juga format ip:port dan ip:port:user:pass (format download Webshare)."""
        if "://" in ln:
            return ln
        parts = ln.split(":")
        if len(parts) == 4:
            host, port, user, pw = parts
            return f"http://{user}:{pw}@{host}:{port}"
        return f"http://{ln}"

    def _health_loop(self):
        while True:
            for p in list(self.proxies):
                if p in self.blacklist:
                    continue
                try:
                    r = requests.get("https://api.ipify.org",
                                     proxies={"http": p, "https": p}, timeout=5)
                    if r.status_code == 200:
                        self._healthy.add(p)
                    else:
                        self._healthy.discard(p)
                except Exception:
                    self._healthy.discard(p)
                    METRICS["proxy_fail"] += 1
            time.sleep(CFG["proxy_health_every"])

    def get(self) -> Optional[dict]:
        if not self.proxies:
            return None
        with self._lock:
            now = time.time()
            if now - self._last_rotate >= self.rotate_seconds or self._current is None:
                pool = [p for p in self.proxies
                        if p in self._healthy and p not in self.blacklist]
                if not pool:
                    pool = [p for p in self.proxies if p not in self.blacklist]
                if not pool:
                    log.error("[Proxy] semua mati")
                    return None
                self._idx = (self._idx + 1) % len(pool)
                self._current = pool[self._idx]
                self._last_rotate = now
            p = self._current
        return {"http": p, "https": p}

    def mark_bad(self, proxy_dict):
        if not proxy_dict: return
        p = proxy_dict.get("http")
        if p:
            self.blacklist.add(p)
            self._healthy.discard(p)
            log.warning(f"[Proxy] blacklist {p}")


# ============================================================
# 1 + 3. SESSION: fallback chain + cookie persistence
# ============================================================

class IGSession:
    def __init__(self, rotator: ProxyRotator):
        self.rotator = rotator
        if creq:
            self._new_cffi_session()
        else:
            log.warning("[Session] curl_cffi tidak ada, profil via HTML kemungkinan gagal")
            self.s = requests.Session()
            self.s.headers.update({"User-Agent": random.choice(CFG["user_agents"])})
        self.s.headers.update({"Accept-Language": "en-US,en;q=0.9"})
        self._last = 0.0
        self._load_cookies()

    API_HEADERS = {"X-IG-App-ID": CFG["app_id"], "Accept": "*/*",
                   "X-Requested-With": "XMLHttpRequest"}

    def _new_cffi_session(self):
        old = getattr(self, "s", None)
        self.impersonate = random.choice(CFG["impersonate_pool"])
        from netutil import session as doh_session  # DNS-over-HTTPS, tahan blokir DNS ISP
        self.s = doh_session(self.impersonate)
        if old is not None:  # bawa cookie anonim ke sesi baru
            for c in getattr(old.cookies, "jar", []):
                self.s.cookies.set(c.name, c.value, domain=c.domain)
        log.info(f"[Session] impersonate={self.impersonate}")

    def _rotate_ua(self):
        if creq and hasattr(self, "impersonate"):
            # ganti profil TLS + UA sekaligus supaya tetap konsisten
            if len(CFG["impersonate_pool"]) > 1:
                self._new_cffi_session()
        elif not creq:
            self.s.headers.update({"User-Agent": random.choice(CFG["user_agents"])})
        self.s.headers.update({"Accept-Language": "en-US,en;q=0.9"})

    def _api_headers(self, referer="https://www.instagram.com/"):
        return {**self.API_HEADERS, "Referer": referer,
                "X-CSRFToken": self.s.cookies.get("csrftoken") or ""}

    def _load_cookies(self):
        if os.path.exists(CFG["state_file"]):
            try:
                with open(CFG["state_file"]) as f:
                    state = json.load(f)
                for c in state.get("cookies", []):
                    self.s.cookies.set(c["name"], c["value"],
                                       domain=c.get("domain", ".instagram.com"))
                log.info(f"[Session] {len(self.s.cookies)} cookies loaded")
            except Exception as e:
                log.warning(f"[Session] load cookie gagal: {e}")

    def save_cookies(self):
        jar = getattr(self.s.cookies, "jar", self.s.cookies)  # curl_cffi vs requests
        with open(CFG["state_file"], "w") as f:
            json.dump({"cookies": [
                {"name": c.name, "value": c.value, "domain": c.domain}
                for c in jar
            ]}, f)

    def _throttle(self):
        lo, hi = CFG["request_jitter"]
        _, jit_mult = _GUARD_MULT()
        w = random.uniform(lo * jit_mult, hi * jit_mult)
        e = time.time() - self._last
        if e < w:
            time.sleep(w - e)
        self._last = time.time()

    def get(self, url, retries=3, **kwargs):
        for attempt in range(retries):
            COMPLIANCE.budget_ok()
            self._throttle()
            proxy = self.rotator.get()
            METRICS["req"] += 1
            try:
                r = self.s.get(url, proxies=proxy, timeout=20, **kwargs)
                if r.status_code == 429:
                    METRICS["429"] += 1
                    _GUARD_RECORD()
                    log.warning(f"429 (attempt {attempt+1}), sleep 60s")
                    time.sleep(60); continue
                if r.status_code == 401:
                    # login wall: retry langsung tidak membantu, cuma bikin IP makin kotor
                    METRICS["401"] += 1
                    _GUARD_RECORD()
                    log.warning(f"401 login wall: {url.split('?')[0]}")
                    return None
                if r.status_code == 403:
                    METRICS["403"] += 1
                    self._rotate_ua()
                    self.rotator.mark_bad(proxy)
                    continue
                METRICS["ok"] += 1
                return r
            except Exception as e:  # requests / curl_cffi punya exception berbeda
                METRICS["err"] += 1
                self.rotator.mark_bad(proxy)
                log.debug(f"err {e}")
                time.sleep(3)
        return None

    # ---------- endpoints ----------
    def profile_info(self, username):
        # 1. halaman profil publik (JSON di <script data-sjs>), tanpa login
        r = self.get(f"https://www.instagram.com/{quote(username)}/")
        if r and r.status_code == 200:
            user = user_from_html(r.text)
            if user:
                return user
        # 2. API lama, sering 401 kalau tidak login
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={quote(username)}"
        r = self.get(url, headers=self._api_headers())
        if r and r.status_code == 200:
            try: return r.json().get("data", {}).get("user")
            except json.JSONDecodeError: pass
        return None

    FEED_DOC_ID = "9310670392322965"

    def user_feed(self, username, count=12, after=None):
        """Feed post via GraphQL doc_id, jalan tanpa login (butuh csrftoken anonim)."""
        variables = {
            "after": after, "before": None,
            "data": {"count": count, "include_reel_media_seen_timestamp": True,
                     "include_relationship_info": True,
                     "latest_besties_reel_media": True, "latest_reel_media": True},
            "first": count, "last": None, "username": username,
            "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
            "__relay_internal__pv__PolarisShareSheetV3relayprovider": True,
        }
        params = {"doc_id": self.FEED_DOC_ID,
                  "variables": json.dumps(variables, separators=(",", ":"))}
        r = self.get("https://www.instagram.com/graphql/query/", params=params,
                     headers=self._api_headers(f"https://www.instagram.com/{username}/"))
        if r and r.status_code == 200:
            try:
                return (r.json().get("data") or {}).get(
                    "xdt_api__v1__feed__user_timeline_graphql_connection")
            except (json.JSONDecodeError, ValueError): pass
        return None

    def related(self, user_id):
        url = f"https://www.instagram.com/api/v1/discover/chaining/?target_id={user_id}"
        r = self.get(url, headers=self._api_headers())
        if r and r.status_code == 200:
            try: return [u["username"] for u in r.json().get("users", [])]
            except Exception: pass
        return []

    def hashtag_info(self, tag):
        url = f"https://www.instagram.com/api/v1/tags/web_info/?tag_name={quote(tag)}"
        r = self.get(url, headers=self._api_headers())
        if r and r.status_code == 200:
            try: return r.json().get("data", {})
            except json.JSONDecodeError: pass
        return None

    def media_comments(self, media_id, count=50):
        url = (f"https://www.instagram.com/api/v1/media/{media_id}/comments/"
               f"?can_support_threading=true&permalink_enabled=false")
        r = self.get(url, headers=self._api_headers())
        if r and r.status_code == 200:
            try:
                return [c.get("text", "") for c in r.json().get("comments", [])[:count]]
            except Exception: pass
        return []


# ============================================================
# 2 + 7. PLAYWRIGHT INTERCEPTOR
# ============================================================

class PlaywrightCapture:
    def __init__(self):
        if sync_playwright is None:
            raise RuntimeError("playwright belum terinstall")
        self.state_file = CFG["state_file"]

    def _context(self, browser):
        if FingerprintGenerator:
            # fingerprint sintetis tapi konsisten (UA, screen, WebGL, codec, dll.)
            fp = FingerprintGenerator(browser="chrome", os=("macos", "windows"),
                                      device="desktop", locale="en-US").generate()
            log.info(f"[PW] fingerprint: {fp.navigator.userAgent[:60]}… "
                     f"{fp.screen.width}x{fp.screen.height}")
            return NewContext(browser, fingerprint=fp)
        kw = {"user_agent": random.choice(CFG["user_agents"]),
              "viewport": {"width": 1280, "height": 900}}
        return browser.new_context(**kw)

    def capture_profile(self, username, max_posts=30, want_reels=False):
        captured = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = self._context(browser)
            page = ctx.new_page()

            def on_resp(resp):
                if "graphql/query" in resp.url or "web_profile_info" in resp.url \
                   or "api/v1/feed" in resp.url or "clips" in resp.url:
                    try:
                        captured.append({"url": resp.url, "json": resp.json()})
                    except Exception: pass

            page.on("response", on_resp)
            page.goto(f"https://www.instagram.com/{username}/", wait_until="networkidle")
            page.wait_for_timeout(2000)
            # kalau tidak login, data profil ada di HTML (data-sjs), bukan di XHR
            captured.append({"html": page.content()})

            for _ in range(max_posts // 12 + 1):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(random.uniform(1500, 3000))

            if want_reels:
                try:
                    page.goto(f"https://www.instagram.com/{username}/reels/",
                              wait_until="networkidle")
                    page.wait_for_timeout(2000)
                    for _ in range(3):
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(1500)
                except Exception: pass

            browser.close()
        return captured

    def capture_hashtag(self, tag, scrolls=3):
        captured = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = self._context(browser)
            page = ctx.new_page()

            def on_resp(resp):
                if "graphql/query" in resp.url or "tags" in resp.url:
                    try:
                        captured.append({"url": resp.url, "json": resp.json()})
                    except Exception: pass

            page.on("response", on_resp)
            page.goto(f"https://www.instagram.com/explore/tags/{tag}/",
                      wait_until="networkidle")
            page.wait_for_timeout(2000)
            for _ in range(scrolls):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1500)
            browser.close()
        return captured


SJS_RE = re.compile(r'<script[^>]*data-sjs[^>]*>(.*?)</script>', re.S)
# all_media_count = null kalau tidak login; jumlah post cuma ada di og:description
OG_POSTS_RE = re.compile(r'<meta property="og:description" content="[^"]*?([\d,.]+[KMB]?) Posts')


def _parse_count(s: str) -> int:
    s = s.replace(",", "")
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(s[-1:], 1)
    try:
        return int(float(s.rstrip("KMB")) * mult)
    except ValueError:
        return 0


def _find_key(o, key):
    if isinstance(o, dict):
        if key in o: return o[key]
        for v in o.values():
            x = _find_key(v, key)
            if x is not None: return x
    elif isinstance(o, list):
        for v in o:
            x = _find_key(v, key)
            if x is not None: return x
    return None


def user_from_html(html: str) -> Optional[dict]:
    """Ambil xig_user_by_username dari HTML profil, ubah ke bentuk yang dipakai parse_profile."""
    for s in SJS_RE.findall(html):
        if "xig_user_by_username" not in s or "follower_count" not in s:
            continue
        try:
            u = _find_key(json.loads(s), "xig_user_by_username")
        except json.JSONDecodeError:
            continue
        if not u:
            continue
        links = u.get("bio_links") or []
        media_count = u.get("all_media_count")
        if media_count is None:
            m = OG_POSTS_RE.search(html)
            media_count = _parse_count(m.group(1)) if m else 0
        return {
            "username": u.get("username"),
            "id": u.get("pk"),
            "full_name": u.get("full_name"),
            "biography": u.get("biography"),
            "external_url": links[0].get("url") if links else "",
            "is_verified": u.get("is_verified"),
            "is_private": u.get("is_private"),
            "profile_pic_url": u.get("profile_pic_url"),
            "edge_followed_by": {"count": u.get("follower_count") or 0},
            "edge_follow": {"count": u.get("following_count") or 0},
            "edge_owner_to_timeline_media": {"count": media_count},
        }
    return None


def extract_from_capture(captured: list) -> Optional[dict]:
    """Cari payload user di response yang ditangkap."""
    for c in captured:
        if c.get("html"):
            u = user_from_html(c["html"])
            if u: return u
            continue
        j = c.get("json", {})
        if "data" in j and "user" in j["data"]:
            return j["data"]["user"]
        if "user" in j and isinstance(j["user"], dict):
            return j["user"]
    return None


# ============================================================
# PARSER
# ============================================================

@dataclass
class Post:
    shortcode: str
    url: str
    media_type: str
    likes: int = 0
    comments: int = 0
    views: int = 0
    caption: str = ""
    taken_at: int = 0
    taken_at_str: str = ""
    media_id: str = ""


@dataclass
class ProfileReport:
    username: str
    user_id: str = ""
    full_name: str = ""
    biography: str = ""
    category: str = ""
    external_url: str = ""
    is_business: bool = False
    is_verified: bool = False
    is_private: bool = False
    profile_pic: str = ""
    business_email: str = ""
    business_phone: str = ""
    emails_in_bio: list = field(default_factory=list)
    phones_in_bio: list = field(default_factory=list)
    website_emails: list = field(default_factory=list)
    followers: int = 0
    following: int = 0
    posts_count: int = 0
    posts: list = field(default_factory=list)
    reels: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    profile_type: str = "UNKNOWN"
    score: float = 0.0
    reasons: list = field(default_factory=list)
    pii_flags: list = field(default_factory=list)
    scraped_at: str = ""


def classify_media(node: dict) -> str:
    tn = node.get("__typename", "")
    pt = node.get("product_type", "")
    if tn == "GraphSidecar": return "carousel"
    if pt == "clips": return "reel"
    if tn == "GraphVideo": return "video"
    return "image"


def node_to_post(node: dict) -> Post:
    sc = node.get("shortcode", "")
    likes = (node.get("edge_liked_by", {}).get("count", 0)
             or node.get("edge_media_preview_like", {}).get("count", 0))
    comments = node.get("edge_media_to_comment", {}).get("count", 0)
    views = node.get("video_view_count", 0) or node.get("video_play_count", 0) or 0
    cap = ""
    ce = node.get("edge_media_to_caption", {}).get("edges", [])
    if ce: cap = ce[0]["node"].get("text", "")
    ts = node.get("taken_at_timestamp", 0)
    return Post(
        shortcode=sc, url=f"https://www.instagram.com/p/{sc}/",
        media_type=classify_media(node), likes=likes, comments=comments,
        views=views, caption=cap, taken_at=ts,
        taken_at_str=(datetime.fromtimestamp(ts, tz=timezone.utc)
                      .strftime("%Y-%m-%d %H:%M UTC") if ts else ""),
        media_id=node.get("id", ""),
    )


def parse_profile(user: dict) -> ProfileReport:
    bio = (user.get("biography") or "").strip()
    return ProfileReport(
        username=user.get("username", ""),
        user_id=str(user.get("id", "")),
        full_name=user.get("full_name", "") or "",
        biography=bio,
        category=user.get("category_name") or user.get("business_category_name") or "",
        external_url=user.get("external_url", "") or "",
        is_business=bool(user.get("is_business_account") or user.get("is_professional_account")),
        is_verified=bool(user.get("is_verified")),
        is_private=bool(user.get("is_private")),
        profile_pic=user.get("profile_pic_url_hd") or user.get("profile_pic_url") or "",
        business_email=user.get("business_email") or user.get("public_email") or "",
        business_phone=user.get("business_phone_number") or user.get("contact_phone_number") or "",
        emails_in_bio=list(set(EMAIL_RE.findall(bio))),
        phones_in_bio=list(set(PHONE_RE.findall(bio))),
        followers=user.get("edge_followed_by", {}).get("count", 0),
        following=user.get("edge_follow", {}).get("count", 0),
        posts_count=user.get("edge_owner_to_timeline_media", {}).get("count", 0),
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )


def v1_node_to_post(node: dict) -> Post:
    """Node dari feed GraphQL baru (format v1)."""
    sc = node.get("code", "")
    if node.get("product_type") == "clips": mt = "reel"
    elif node.get("media_type") == 8: mt = "carousel"
    elif node.get("media_type") == 2: mt = "video"
    else: mt = "image"
    ts = node.get("taken_at") or 0
    return Post(
        shortcode=sc, url=f"https://www.instagram.com/p/{sc}/",
        media_type=mt, likes=node.get("like_count") or 0,
        comments=node.get("comment_count") or 0,
        views=node.get("view_count") or node.get("play_count") or 0,
        caption=(node.get("caption") or {}).get("text", "") or "",
        taken_at=ts,
        taken_at_str=(datetime.fromtimestamp(ts, tz=timezone.utc)
                      .strftime("%Y-%m-%d %H:%M UTC") if ts else ""),
        media_id=str(node.get("pk", "")),
    )


def collect_posts(session: IGSession, username: str, max_posts=30):
    posts, reels = [], []
    cursor, fetched = None, 0
    while fetched < max_posts:
        media = session.user_feed(username, 12, cursor)
        if not media: break
        edges = media.get("edges", [])
        if not edges: break
        for e in edges:
            p = v1_node_to_post(e["node"])
            if p.media_type == "reel": reels.append(p)
            posts.append(p)
            fetched += 1
            if fetched >= max_posts: break
        pi = media.get("page_info", {})
        if not pi.get("has_next_page"): break
        cursor = pi.get("end_cursor")
        if not cursor: break
    return posts, reels


# ============================================================
# 9. STATS
# ============================================================

def compute_stats(pr: ProfileReport) -> dict:
    posts = pr.posts
    reels = pr.reels
    followers = max(pr.followers, 1)
    if not posts:
        return {"sampled_posts": 0, "engagement_rate": 0.0,
                "avg_likes": 0, "avg_comments": 0, "avg_views_reels": 0}
    likes = [p.likes for p in posts]
    comments = [p.comments for p in posts]
    avg_l = sum(likes) / len(likes)
    avg_c = sum(comments) / len(comments)
    er = ((avg_l + avg_c) / followers) * 100
    rv = [r.views for r in reels if r.views]
    avg_v = sum(rv) / len(rv) if rv else 0
    ts = sorted(p.taken_at for p in posts if p.taken_at)
    freq = 0.0
    if len(ts) >= 2:
        span = (ts[-1] - ts[0]) / 86400
        if span > 0: freq = round(len(ts) / span * 7, 2)
    by_day, by_hour = Counter(), Counter()
    for p in posts:
        if p.taken_at:
            d = datetime.fromtimestamp(p.taken_at, tz=timezone.utc)
            by_day[d.strftime("%A")] += 1
            by_hour[d.hour] += 1
    top = sorted(posts, key=lambda x: x.likes, reverse=True)[:5]
    return {
        "sampled_posts": len(posts),
        "sampled_reels": len(reels),
        "avg_likes": round(avg_l, 1),
        "avg_comments": round(avg_c, 1),
        "avg_views_reels": round(avg_v, 1),
        "engagement_rate": round(er, 3),
        "posts_per_week": freq,
        "media_mix": dict(Counter(p.media_type for p in posts)),
        "best_day": by_day.most_common(1)[0][0] if by_day else "",
        "best_hour_utc": by_hour.most_common(1)[0][0] if by_hour else None,
        "top_posts": [{"url": p.url, "likes": p.likes, "comments": p.comments}
                      for p in top],
        "followers_to_following_ratio": round(pr.followers / max(pr.following, 1), 2),
    }


# ============================================================
# CLASSIFIER
# ============================================================

B2B_KW = ["agency", "consultant", "consulting", "b2b", "saas", "solution",
          "enterprise", "startup", "corporate", "company", "vendor", "supplier",
          "distributor", "manufacture", "logistics", "software house",
          "it solution", "digital agency", "b2b service"]
COMPANY_KW = ["pt ", "cv ", "inc", "ltd", "corp", "group", "holdings", "tbk"]
KOL_KW = ["kol", "influencer", "creator", "content creator", "endorsement",
          "review", "reviewer", "vlogger", "blogger"]


def classify(pr: ProfileReport):
    text = f"{pr.username} {pr.full_name} {pr.biography} {pr.category}".lower()
    reasons, score = [], 0.0
    b2b = [k for k in B2B_KW if k in text]
    co = [k for k in COMPANY_KW if k in text]
    kol = [k for k in KOL_KW if k in text]
    if pr.is_business: score += 3; reasons.append("is_business")
    if b2b: score += 3; reasons.append(f"b2b:{','.join(b2b[:3])}")
    if co: score += 4; reasons.append(f"co:{','.join(co[:3])}")
    if kol: score += 3; reasons.append(f"kol:{','.join(kol[:3])}")
    if pr.business_email or pr.emails_in_bio: score += 2; reasons.append("has_email")
    if pr.is_verified: score += 1; reasons.append("verified")
    if score >= 6 and (co or b2b):
        t = "B2B" if b2b and not co else "COMPANY"
    elif kol or (pr.is_verified and pr.followers > 10000 and not pr.is_business):
        t = "KOL"
    elif score >= 3:
        t = "COMPANY"
    else:
        t = "PERSONAL"
    return t, round(score, 2), reasons


# ============================================================
# 8. WEBSITE EMAIL ENRICHMENT
# ============================================================

def enrich_website(url: str) -> list:
    if not url or "instagram.com" in url: return []
    try:
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": random.choice(CFG["user_agents"])})
        emails = set(EMAIL_RE.findall(r.text))
        # cek halaman /contact dan /about
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        for path in ("/contact", "/about", "/kontak"):
            try:
                rr = requests.get(base + path, timeout=8,
                                  headers={"User-Agent": random.choice(CFG["user_agents"])})
                emails |= set(EMAIL_RE.findall(rr.text))
            except Exception: pass
        return list(emails)[:10]
    except Exception:
        return []


# ============================================================
# 10. COMMENT INTENT MINING
# ============================================================

INTENT_KW = ["harga", "price", "dm", "order", "beli", "how much",
             "pricelist", "minat", "info", "wa", "whatsapp"]


def mine_intent(session: IGSession, posts: list, top_n=3) -> list:
    hits = []
    top = sorted(posts, key=lambda p: p.likes, reverse=True)[:top_n]
    for p in top:
        if not p.media_id: continue
        comments = session.media_comments(p.media_id)
        for c in comments:
            cl = c.lower()
            if any(k in cl for k in INTENT_KW):
                hits.append({"post": p.url, "comment": c[:200]})
    return hits[:50]


# ============================================================
# 13. SQLITE STORAGE + GROWTH
# ============================================================

class Storage:
    def __init__(self, path=CFG["db_path"]):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                username TEXT PRIMARY KEY,
                data TEXT,
                followers INTEGER,
                last_scraped TEXT,
                followers_history TEXT
            )""")
        self.conn.commit()
        self._lock = threading.Lock()

    def upsert(self, pr: ProfileReport):
        with self._lock:
            cur = self.conn.execute(
                "SELECT followers_history FROM profiles WHERE username=?",
                (pr.username,))
            row = cur.fetchone()
            history = json.loads(row[0]) if row and row[0] else []
            from datastore import snapshot  # hanya catat kalau angka berubah / gagal (0) tidak dicatat
            history = snapshot(history, {"followers": pr.followers})
            history = history[-30:]  # simpan 30 snapshot terakhir
            self.conn.execute("""
                INSERT INTO profiles (username, data, followers, last_scraped, followers_history)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                  data=excluded.data,
                  followers=excluded.followers,
                  last_scraped=excluded.last_scraped,
                  followers_history=excluded.followers_history
            """, (pr.username, json.dumps(asdict(pr), ensure_ascii=False),
                  pr.followers, pr.scraped_at, json.dumps(history)))
            self.conn.commit()

    def growth(self, username: str) -> dict:
        cur = self.conn.execute(
            "SELECT followers_history FROM profiles WHERE username=?", (username,))
        row = cur.fetchone()
        if not row: return {}
        h = json.loads(row[0] or "[]")
        if len(h) < 2: return {"samples": len(h)}
        first, last = h[0]["followers"], h[-1]["followers"]
        days = (datetime.fromisoformat(h[-1]["ts"].rstrip("Z")) -
                datetime.fromisoformat(h[0]["ts"].rstrip("Z"))).days or 1
        return {"samples": len(h), "delta": last - first,
                "pct": round((last - first) / max(first, 1) * 100, 2),
                "per_day": round((last - first) / days, 1)}

    def needs_refresh(self, username: str, hours=24) -> bool:
        cur = self.conn.execute(
            "SELECT last_scraped FROM profiles WHERE username=?", (username,))
        row = cur.fetchone()
        if not row or not row[0]: return True
        try:
            ts = datetime.fromisoformat(row[0].rstrip("Z"))
            return (datetime.now(timezone.utc) - ts).total_seconds() > hours * 3600
        except Exception:
            return True


# ============================================================
# 14. ENTITY RESOLUTION
# ============================================================

def dedupe_entities(reports: list) -> list:
    if not fuzz:
        log.warning("[Dedupe] rapidfuzz tidak ada, skip")
        return reports
    merged = []
    for r in reports:
        dup = None
        for m in merged:
            same_domain = False
            if r.external_url and m.external_url:
                try:
                    same_domain = urlparse(r.external_url).netloc == urlparse(m.external_url).netloc
                except Exception: pass
            if fuzz.ratio(r.full_name.lower(), m.full_name.lower()) > 85 or same_domain:
                dup = m; break
        if dup:
            log.info(f"[Dedupe] merge @{r.username} -> @{dup.username}")
            dup.posts.extend(r.posts)
            dup.reels.extend(r.reels)
            dup.reasons.append(f"merged:{r.username}")
        else:
            merged.append(r)
    return merged


# ============================================================
# 5 + 6. DISCOVERY
# ============================================================

def discover_by_hashtag(session: IGSession, tag: str, limit=30) -> list:
    found = []
    info = session.hashtag_info(tag)
    if not info:
        # fallback Playwright
        try:
            cap = PlaywrightCapture().capture_hashtag(tag)
            for c in cap:
                j = c.get("json", {})
                edges = (j.get("data", {}).get("hashtag", {})
                         .get("edge_hashtag_to_media", {}).get("edges", []))
                for e in edges:
                    owner = e.get("node", {}).get("owner", {}).get("username")
                    if owner and owner not in found:
                        found.append(owner)
                        if len(found) >= limit: return found
        except Exception as e:
            log.warning(f"[Discovery] hashtag playwright gagal: {e}")
        return found
    # via API
    try:
        edges = (info.get("data", {}).get("hashtag", {})
                 .get("edge_hashtag_to_media", {}).get("edges", []))
        for e in edges:
            owner = e.get("node", {}).get("owner", {}).get("username")
            if owner and owner not in found:
                found.append(owner)
                if len(found) >= limit: break
    except Exception: pass
    return found


def discover_related(session: IGSession, username: str, limit=20) -> list:
    user = session.profile_info(username)
    if not user: return []
    uid = str(user.get("id", ""))
    if not uid: return []
    return session.related(uid)[:limit]


# ============================================================
# 11. ASYNC BATCH (httpx)
# ============================================================

async def fetch_many_async(urls: list, headers: dict, concurrency=3) -> list:
    if httpx is None:
        log.warning("[Async] httpx tidak ada")
        return []
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(http2=True, headers=headers, timeout=20) as client:
        async def one(u):
            async with sem:
                await asyncio.sleep(random.uniform(2, 5))
                try:
                    r = await client.get(u)
                    return r.status_code, r.text[:500]
                except Exception as e:
                    return 0, str(e)
        return await asyncio.gather(*[one(u) for u in urls])


# ============================================================
# 15. ALERTS
# ============================================================

def alert(pr: ProfileReport):
    if pr.profile_type in ("B2B", "COMPANY", "KOL") and pr.score >= 6:
        msg = (f"🎯 @{pr.username} [{pr.profile_type}] score={pr.score} "
               f"followers={pr.followers} ER={pr.stats.get('engagement_rate', 0)}%")
        if CFG["telegram_token"] and CFG["telegram_chat"]:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{CFG['telegram_token']}/sendMessage",
                    json={"chat_id": CFG["telegram_chat"], "text": msg}, timeout=8)
            except Exception as e:
                log.warning(f"[Alert] TG gagal: {e}")
        if CFG["slack_webhook"]:
            try:
                requests.post(CFG["slack_webhook"], json={"text": msg}, timeout=8)
            except Exception as e:
                log.warning(f"[Alert] Slack gagal: {e}")


# ============================================================
# PIPELINE UTAMA
# ============================================================

def scrape_one(session: IGSession, storage: Storage, username: str,
               max_posts=30, mine_comments=False) -> Optional[ProfileReport]:
    if COMPLIANCE.is_opted_out(username):
        log.info(f"[Skip] @{username} opted-out")
        return None

    log.info(f"[Scrape] @{username}")
    user = session.profile_info(username)
    source = "api"

    if not user:
        # fallback: Playwright interceptor
        log.info(f"[Fallback] Playwright untuk @{username}")
        try:
            cap = PlaywrightCapture().capture_profile(
                username, max_posts=max_posts, want_reels=True)
            user = extract_from_capture(cap)
            source = "playwright"
        except Exception as e:
            log.warning(f"[Fallback] gagal: {e}")

    if not user:
        log.warning(f"[!] @{username} tidak bisa diambil")
        return None

    pr = parse_profile(user)
    pr.reasons.append(f"source:{source}")

    if pr.is_private:
        log.info(f"[Skip] @{username} private")
        pr.stats = {"private": True}
        pr.pii_flags = Compliance.pii_flags(asdict(pr))
        storage.upsert(pr)
        return pr

    # posts & reels
    posts, reels = collect_posts(session, pr.username, max_posts)
    pr.posts = posts
    pr.reels = reels
    if not posts and pr.posts_count > 0:
        log.warning(f"[Feed] @{username} punya {pr.posts_count} post tapi feed kosong, "
                    f"FEED_DOC_ID ({IGSession.FEED_DOC_ID}) mungkin sudah diganti IG")

    # enrich website
    if pr.external_url:
        pr.website_emails = enrich_website(pr.external_url)

    # stats + classify
    pr.stats = compute_stats(pr)
    pr.profile_type, pr.score, cls_reasons = classify(pr)
    pr.reasons.extend(cls_reasons)

    # comment intent
    if mine_comments and posts:
        intents = mine_intent(session, posts)
        pr.stats["comment_intents"] = intents

    # compliance flag
    pr.pii_flags = Compliance.pii_flags(asdict(pr))
    pr.stats["growth"] = storage.growth(pr.username)

    storage.upsert(pr)
    alert(pr)

    log.info(f"[OK] @{pr.username} type={pr.profile_type} score={pr.score} "
             f"followers={pr.followers} sampled={len(posts)} "
             f"ER={pr.stats.get('engagement_rate')}%")
    return pr


# ============================================================
# EXPORT
# ============================================================

def export_json(reports, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in reports], f, indent=2, ensure_ascii=False)
    log.info(f"[Export] JSON -> {path}")


def export_csv(reports, path):
    if not reports: return
    fields = ["username", "full_name", "category", "profile_type", "score",
              "followers", "following", "posts_count",
              "engagement_rate", "avg_likes", "avg_comments", "avg_views_reels",
              "posts_per_week", "followers_to_following_ratio",
              "best_day", "best_hour_utc",
              "is_business", "is_verified", "is_private",
              "business_email", "business_phone", "external_url",
              "pii_flags", "reasons"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in reports:
            d = asdict(r)
            row = {k: d.get(k, "") for k in fields}
            s = d.get("stats", {})
            row["engagement_rate"] = s.get("engagement_rate", "")
            row["avg_likes"] = s.get("avg_likes", "")
            row["avg_comments"] = s.get("avg_comments", "")
            row["avg_views_reels"] = s.get("avg_views_reels", "")
            row["posts_per_week"] = s.get("posts_per_week", "")
            row["followers_to_following_ratio"] = s.get("followers_to_following_ratio", "")
            row["best_day"] = s.get("best_day", "")
            row["best_hour_utc"] = s.get("best_hour_utc", "")
            row["pii_flags"] = "|".join(d.get("pii_flags", []))
            row["reasons"] = "|".join(d.get("reasons", []))
            w.writerow(row)
    log.info(f"[Export] CSV -> {path}")


def print_metrics():
    log.info(f"[Metrics] {dict(METRICS)}")


# ============================================================
# MAIN
# ============================================================

def build_session() -> tuple[IGSession, Storage, ProxyRotator]:
    rotator = ProxyRotator(CFG["proxy_file"], CFG["proxy_rotate_seconds"])
    session = IGSession(rotator)
    storage = Storage()
    return session, storage, rotator


def run_once(targets: list, args, out_dir: str):
    session, storage, _ = build_session()
    reports = []
    for t in targets:
        if not storage.needs_refresh(t, hours=args.refresh_hours):
            log.info(f"[Skip] @{t} masih fresh (diff mode)")
            continue
        r = scrape_one(session, storage, t,
                       max_posts=args.max_posts,
                       mine_comments=args.mine_comments)
        if r: reports.append(r)

    reports = dedupe_entities(reports)

    os.makedirs(out_dir, exist_ok=True)
    export_json(reports, f"{out_dir}/profiles.json")
    export_csv(reports, f"{out_dir}/profiles.csv")

    session.save_cookies()
    print_metrics()

    print("\n=== RINGKASAN ===")
    for r in reports:
        print(f"@{r.username:<25} {r.profile_type:<10} score={r.score:<5} "
              f"ER={r.stats.get('engagement_rate', 0):>6}% followers={r.followers}")


def run_discovery(args, out_dir):
    session, storage, _ = build_session()
    candidates = set()

    if args.hashtags:
        for tag in args.hashtags.split(","):
            tag = tag.strip().lstrip("#")
            log.info(f"[Discovery] hashtag #{tag}")
            found = discover_by_hashtag(session, tag, args.discover_limit)
            candidates.update(found)
            log.info(f"  -> {len(found)} kandidat")

    if args.related_from:
        for seed in args.related_from.split(","):
            seed = seed.strip().lstrip("@")
            log.info(f"[Discovery] related dari @{seed}")
            found = discover_related(session, seed, args.discover_limit)
            candidates.update(found)
            log.info(f"  -> {len(found)} kandidat")

    log.info(f"[Discovery] total kandidat unik: {len(candidates)}")
    candidates = sorted(candidates)

    # saring: hanya yang belum ada di DB atau perlu refresh
    todo = [c for c in candidates if storage.needs_refresh(c, args.refresh_hours)][:args.max_targets]
    log.info(f"[Discovery] akan scrape {len(todo)} kandidat")

    # reuse run_once
    args.targets = ",".join(todo)
    run_once(todo, args, out_dir)


def run_schedule(args, out_dir):
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        log.error("APScheduler belum terinstall: pip install apscheduler")
        return

    interval_map = {"1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400}
    secs = interval_map.get(args.schedule, 21600)

    targets = []
    if args.targets:
        targets = [t.strip().lstrip("@") for t in args.targets.split(",")]
    elif args.targets_file and os.path.exists(args.targets_file):
        with open(args.targets_file) as f:
            targets = [ln.strip().lstrip("@") for ln in f if ln.strip()]

    if not targets:
        log.error("Butuh --targets atau --targets-file untuk scheduling")
        return

    sch = BlockingScheduler()
    sch.add_job(lambda: run_once(targets, args, out_dir),
                "interval", seconds=secs)
    log.info(f"[Scheduler] jalan tiap {secs}s, {len(targets)} target. Ctrl+C stop.")
    run_once(targets, args, out_dir)
    try:
        sch.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler berhenti.")


def print_report(out_dir):
    storage = Storage()
    cur = storage.conn.execute(
        "SELECT username, followers, last_scraped, followers_history FROM profiles")
    print(f"{'username':<25} {'followers':>10} {'delta':>8} {'last_scraped':<25}")
    print("-" * 75)
    for u, f, ls, h in cur.fetchall():
        hist = json.loads(h or "[]")
        delta = hist[-1]["followers"] - hist[0]["followers"] if len(hist) >= 2 else 0
        print(f"@{u:<24} {f:>10} {delta:>+8} {ls:<25}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["direct", "mobile", "webshare", "custom"],
                    help="Mode operasi (auto-detect kalau tidak diisi)")
    ap.add_argument("--proxy-file", help="Override file proxy")
    ap.add_argument("--targets", help="comma-separated username")
    ap.add_argument("--targets-file", help="file berisi username per baris")
    ap.add_argument("--hashtags", help="comma-separated hashtag (tanpa #)")
    ap.add_argument("--related-from", help="comma-separated seed username")
    ap.add_argument("--discover-limit", type=int, default=30)
    ap.add_argument("--max-targets", type=int, default=50)
    ap.add_argument("--max-posts", type=int, default=None,
                    help="default mengikuti mode")
    ap.add_argument("--mine-comments", action="store_true")
    ap.add_argument("--refresh-hours", type=int, default=24)
    ap.add_argument("--out", default="hasil")
    ap.add_argument("--schedule", help="1h|6h|12h|24h")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--async-demo", action="store_true")
    args = ap.parse_args()

    if args.report:
        return print_report(args.out)

    # inject config dari mode
    resolved = resolve_mode(args.mode, args.proxy_file)
    CFG.update({
        "proxy_file": resolved["proxy_file"],
        "request_jitter": resolved["request_jitter"],
        "max_requests_per_hour": resolved["max_requests_per_hour"],
    })
    if args.max_posts is None:
        args.max_posts = resolved["max_posts_default"]

    if args.async_demo:
        headers = {"User-Agent": random.choice(CFG["user_agents"])}
        urls = [f"https://www.instagram.com/{u}/" for u in
                (args.targets or "instagram").split(",")]
        asyncio.run(fetch_many_async(urls, headers))
        return

    if args.schedule:
        return run_schedule(args, args.out)

    # discovery mode
    if args.hashtags or args.related_from:
        return run_discovery(args, args.out)

    # direct targets
    targets = []
    if args.targets:
        targets = [t.strip().lstrip("@") for t in args.targets.split(",")]
    elif args.targets_file:
        with open(args.targets_file) as f:
            targets = [ln.strip().lstrip("@") for ln in f if ln.strip()]

    if not targets:
        ap.error("Butuh --targets, --targets-file, --hashtags, atau --related-from")

    run_once(targets, args, args.out)


if __name__ == "__main__":
    main()