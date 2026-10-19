"""
ig_modes.py, Auto-tune config berdasarkan mode.
Di-import oleh ig_scraper.py; nilainya meng-override CFG di main().
"""

import os
import threading
import time
import logging

log = logging.getLogger("modes")

MODES = {
    "direct": {
        "proxy_file": None,               # tidak ada proxy
        "request_jitter": (8.0, 15.0),
        "max_requests_per_hour": 30,
        "max_posts_default": 12,
        "description": "IP rumah/HP sendiri, paling aman, gratis",
    },
    "mobile": {
        "proxy_file": None,
        "request_jitter": (5.0, 10.0),
        "max_requests_per_hour": 60,
        "max_posts_default": 20,
        "description": "Hotspot HP, reputasi IP seluler terbaik",
    },
    "webshare": {
        "proxy_file": "proxies_webshare.txt",
        "request_jitter": (5.0, 8.0),
        "max_requests_per_hour": 40,
        "max_posts_default": 20,
        "description": "Webshare free tier (10 DC proxy), untuk testing rotator",
    },
    "custom": {
        "proxy_file": "proxies.txt",
        "request_jitter": (2.5, 5.5),
        "max_requests_per_hour": 200,
        "max_posts_default": 30,
        "description": "Proxy manual (ISP/residential berbayar), Level 3 nanti",
    },
}


def count_proxies(path) -> int:
    """Jumlah baris proxy (bukan komentar / baris kosong) di file."""
    if not path or not os.path.exists(path):
        return 0
    with open(path) as f:
        return sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))


def resolve_mode(cli_mode: str = None, cli_proxy_file: str = None) -> dict:
    """
    Tentukan mode final + config.
    Prioritas: CLI flag > env var > auto-detect > default 'direct'.
    """
    # 1. CLI eksplisit
    if cli_mode and cli_mode in MODES:
        mode = cli_mode

    # 2. Env var
    elif os.getenv("IG_MODE") in MODES:
        mode = os.getenv("IG_MODE")

    # 3. Auto-detect: hanya kalau file proxy benar-benar berisi proxy
    elif count_proxies(cli_proxy_file) or count_proxies("proxies.txt"):
        mode = "custom"
    elif count_proxies("proxies_webshare.txt"):
        mode = "webshare"
    else:
        mode = "direct"

    cfg = dict(MODES[mode])
    if cli_proxy_file:
        cfg["proxy_file"] = cli_proxy_file

    # mode proxy tanpa proxy = jalan di IP rumah dengan setting agresif -> tolak
    if cfg["proxy_file"] and not count_proxies(cfg["proxy_file"]):
        raise SystemExit(f"[Mode] {mode} butuh proxy, tapi {cfg['proxy_file']} "
                         f"kosong / tidak ada. Isi dulu, atau pakai --mode direct.")

    log.info(f"[Mode] {mode}: {cfg['description']}")
    log.info(f"       jitter={cfg['request_jitter']}s "
             f"budget={cfg['max_requests_per_hour']}/jam "
             f"proxy={cfg['proxy_file'] or 'NONE'}")

    return {"mode": mode, **cfg}


def runtime_guard():
    """
    Watcher: kalau 429 muncul >= 3x dalam 10 menit, otomatis turunkan budget
    dan perpanjang jitter. Ini 'rem darurat' supaya tidak makin parah.
    """
    state = {"hits": [], "budget_multiplier": 1.0, "jitter_multiplier": 1.0}
    lock = threading.Lock()

    def record_429():
        with lock:
            now = time.time()
            state["hits"] = [t for t in state["hits"] if now - t < 600]
            state["hits"].append(now)
            n = len(state["hits"])
            if n >= 3:
                state["budget_multiplier"] = max(0.3, state["budget_multiplier"] * 0.7)
                state["jitter_multiplier"] = min(3.0, state["jitter_multiplier"] * 1.3)
                log.warning(f"[Guard] 429 x{n} → budget x{state['budget_multiplier']:.2f}, "
                            f"jitter x{state['jitter_multiplier']:.2f}")

    def get_multipliers():
        with lock:
            return state["budget_multiplier"], state["jitter_multiplier"]

    return record_429, get_multipliers
