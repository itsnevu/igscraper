from datetime import datetime, timedelta, timezone

import datastore as ds


def test_norm_phone():
    assert ds.norm_phone("0813-1891 1529") == "6281318911529"
    assert ds.norm_phone("+62 813 1891 1529") == "6281318911529"
    assert ds.norm_phone("813 1891 1529") == "6281318911529"
    assert ds.norm_phone("12345") == ""
    assert ds.norm_phone(None) == ""


def test_is_mobile():
    assert ds.is_mobile("6281318911529")
    assert not ds.is_mobile("62217654321")  # telepon kantor Jakarta


def test_norm_url_buang_tracking():
    assert ds.norm_url("www.Kopi.com/menu/?utm_source=ig#top") == "https://kopi.com/menu"
    assert ds.norm_url("") == ""
    assert ds.norm_url("bukan-url") == ""


def test_norm_ig():
    assert ds.norm_ig("@Kopi.Kenangan/") == "kopi.kenangan"
    assert ds.norm_ig("https://instagram.com/kopi_x?igshid=abc") == "kopi_x"
    assert ds.norm_ig("a..b") == ""
    assert ds.norm_ig("") == ""


def test_dedupe_gabung_place_id_sama():
    a = {"place_id": "X", "name": "Kopi A", "reviews": 10}
    b = {"place_id": "X", "name": "Kopi A", "reviews": 50, "website": "https://a.com"}
    out, merged = ds.dedupe([a, b])
    assert merged == 1 and len(out) == 1
    assert out[0]["reviews"] == 50 and out[0]["website"] == "https://a.com"


def test_dedupe_lokasi_jauh_tidak_digabung():
    a = {"place_id": "1", "name": "Kopi A", "name_key": "kopi a", "reviews": 5, "lat": -6.2, "lon": 106.8}
    b = {"place_id": "2", "name": "Kopi A", "name_key": "kopi a", "reviews": 5, "lat": -6.9, "lon": 107.6}
    out, merged = ds.dedupe([a, b])
    assert merged == 0 and len(out) == 2


def test_snapshot_tidak_duplikat():
    h = ds.snapshot([], {"reviews": 10})
    assert len(h) == 1
    assert len(ds.snapshot(h, {"reviews": 11})) == 1  # < 6 jam: tidak menambah
    old = [{"ts": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(), "reviews": 10}]
    assert len(ds.snapshot(old, {"reviews": 10})) == 1  # tidak berubah, belum seminggu
    assert len(ds.snapshot(old, {"reviews": 12})) == 2  # berubah
    assert ds.snapshot([], {"reviews": 0}) == []  # data kosong tidak dicatat
