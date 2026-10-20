import pytest

import market


def biz(**kw):
    base = {"_detailed": True, "name": "Kopi Kecil", "rating": 4.7, "reviews": 120,
            "phone": "081318911529", "maps_url": "https://maps.google.com/?cid=1"}
    return {**base, **kw}


def test_lead_score_belum_detail():
    assert market.lead_score({"name": "x", "reviews": 10}) == (None, [])


def test_lead_score_prospek_ideal_tinggi():
    s, why = market.lead_score(biz())
    assert s >= 80
    assert "bisa WhatsApp" in why and "tanpa website" in why


def test_lead_score_pemain_besar_dan_cabang_rendah():
    ideal, _ = market.lead_score(biz())
    big, why = market.lead_score(biz(reviews=3000))
    chain, why2 = market.lead_score(biz(branches_in_area=3))
    assert big < ideal and "pemain besar" in why
    assert chain < ideal and "jaringan bercabang" in why2


def test_lead_score_sudah_digital_lebih_rendah():
    no_gap, _ = market.lead_score(biz(website="https://a.com", instagram="kopikecil"))
    gap, _ = market.lead_score(biz())
    assert no_gap < gap


def test_lead_score_tanpa_kontak_dibatasi():
    s, why = market.lead_score(biz(phone=None))
    assert s <= 20 and "tidak ada kontak" in why


def test_lead_score_rentang():
    for p in (biz(), biz(reviews=5000, branches_in_area=9, rating=3.0), biz(emails=["a@b.co"])):
        s, _ = market.lead_score(p)
        assert 0 <= s <= 100


def test_build_report_ranking_dan_prospek():
    places = [biz(place_id=str(i), name=f"Kopi {i}", reviews=r, rating=4.6)
              for i, r in enumerate([2000, 500, 100, 40, 10])]
    rep = market.score_all(market.build_report("coffee shop", "Kemang", places))
    s = rep["summary"]
    assert s["businesses"] == 5 and s["total_reviews"] == 2650
    assert rep["leaders"][0]["name"] == "Kopi 0"
    assert all("lead_score" in p for p in rep["all"])
    assert all(p["name"] != "Kopi 0" for p in rep["prospects"])  # >800 ulasan bukan prospek
    scores = [p["score"] for p in rep["prospects"]]
    assert scores == sorted(scores, reverse=True)


def test_compare_skor_peluang(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "DB", str(tmp_path / "t.db"))
    import json
    conn = market.db()
    for area, n, rev in (("A", 10, 5000), ("B", 50, 1000)):
        rep = {"created": "2026-01-01T00:00:00+00:00",
               "summary": {"businesses": n, "avg_rating": 4.5, "median_reviews": 10, "total_reviews": rev,
                           "top3_share": 30},
               "digital": {"website": 40, "whatsapp": 50}}
        conn.execute("INSERT INTO market_reports (industry, area, created, report) VALUES (?,?,?,?)",
                     ("kopi", area, rep["created"], json.dumps(rep)))
    conn.commit(); conn.close()
    res = market.compare("kopi", ["A", "B", "C"])
    assert [a["area"] for a in res["areas"]] == ["A", "B"]  # A: sedikit pemain, permintaan tinggi


def test_status_lead_tersimpan(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "DB", str(tmp_path / "t.db"))
    market.set_lead("k1", "Kopi", "dihubungi")
    market.set_lead("k1", "Kopi", "deal")
    assert market.get_leads()["k1"]["status"] == "deal"
    with pytest.raises(ValueError):
        market.set_lead("k1", "Kopi", "ngawur")


def _save_report(industry, area, places, created="2026-01-01T00:00:00+00:00"):
    import json
    rep = market.build_report(industry, area, places)
    rep["created"] = created
    conn = market.db()
    conn.execute("INSERT INTO market_reports (industry, area, created, report) VALUES (?,?,?,?)",
                 (industry, area, created, json.dumps(rep)))
    conn.commit(); conn.close()


def test_lead_catatan_dan_pengingat(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "DB", str(tmp_path / "t.db"))
    market.set_lead("k1", "Kopi", "dihubungi", note="minta sore", remind="2026-11-01")
    market.set_lead("k1", "Kopi", "tertarik")  # note/remind None = tidak dihapus
    got = market.get_leads()["k1"]
    assert got == {**got, "status": "tertarik", "note": "minta sore", "remind": "2026-11-01"}
    with pytest.raises(ValueError):
        market.set_lead("k1", "Kopi", "baru", remind="besok")


def test_prospect_board_gabung_wilayah(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "DB", str(tmp_path / "t.db"))
    _save_report("kopi", "A", [biz(place_id="1", name="Kopi Satu"), biz(place_id="2", name="Besar", reviews=5000)])
    _save_report("kopi", "B", [biz(place_id="1", name="Kopi Satu")])
    board = market.prospect_board()
    assert [p["key"] for p in board] == ["1"]  # duplikat lintas wilayah digabung, pemain besar tidak masuk
    market.set_lead("2", "Besar", "dihubungi")
    assert {p["key"] for p in market.prospect_board()} == {"1", "2"}  # yang sudah ditindaklanjuti tetap tampil


def test_report_changes():
    prev = {"all": [biz(place_id="1", rating=4.5, reviews=100), biz(place_id="2"), biz(place_id="3", rating=4.8)]}
    cur = {"all": [biz(place_id="1", rating=4.7, reviews=130), biz(place_id="3", rating=4.5), biz(place_id="9")]}
    c = market.report_changes(prev, cur)
    assert [p["place_id"] for p in c["new"]] == ["9"]
    assert [p["place_id"] for p in c["gone"]] == ["2"]
    assert c["rating_up"][0]["delta"] == 0.2 and c["rating_down"][0]["delta"] == -0.3
    assert c["reviews_jump"][0]["delta"] == 30


def test_watch_overview_dua_riset(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "DB", str(tmp_path / "t.db"))
    _save_report("kopi", "A", [biz(place_id="1")], "2026-01-01T00:00:00+00:00")
    _save_report("kopi", "A", [biz(place_id="1"), biz(place_id="2")], "2026-01-08T00:00:00+00:00")
    _save_report("kopi", "B", [biz(place_id="5")])
    ov = market.watch_overview()
    a = next(x for x in ov if x["area"] == "A")
    assert a["runs"] == 2 and len(a["changes"]["new"]) == 1
    assert next(x for x in ov if x["area"] == "B")["changes"] is None
