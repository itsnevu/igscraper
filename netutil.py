"""
netutil.py, HTTP client bersama yang tahan blokir DNS ISP.

ISP Indonesia memblokir situs dengan membelokkan DNS ke server blokir, sehingga
muncul error SSL ("no alternative certificate subject name", "self signed").
Di sini nama domain di-resolve lewat DNS-over-HTTPS (Cloudflare/Google), jadi
tidak bisa dibelokkan. Verifikasi sertifikat TETAP aktif, koneksi tetap aman.

Tidak menembus blokir berbasis SNI/DPI; untuk itu perlu VPN/proxy.
"""
import time

from curl_cffi import CurlOpt
from curl_cffi import requests as creq

DOH_SERVERS = [b"https://1.1.1.1/dns-query", b"https://8.8.8.8/dns-query"]


def session(impersonate="chrome", doh=0):
    """curl_cffi Session dengan DNS-over-HTTPS."""
    return creq.Session(impersonate=impersonate,
                        curl_options={CurlOpt.DOH_URL: DOH_SERVERS[doh % len(DOH_SERVERS)]})


def request(method, url, retries=3, **kw):
    """GET/POST dengan DoH + retry; ganti server DoH kalau gagal."""
    kw.setdefault("timeout", 20)
    kw.setdefault("impersonate", "chrome")
    last = None
    for i in range(retries):
        try:
            opts = {CurlOpt.DOH_URL: DOH_SERVERS[i % len(DOH_SERVERS)]}
            return creq.request(method, url, curl_options=opts, **kw)
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def get(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)
