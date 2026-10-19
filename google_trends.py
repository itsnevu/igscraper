"""
Tren pencarian Google untuk keyword kompetitor (gratis, via pytrends).

Usage:
    python google_trends.py "kopi kenangan" "janji jiwa" "fore coffee"
    python google_trends.py --timeframe "today 12-m" kompetitor_a kompetitor_b
Output: trends.csv
"""
import argparse
import time

from pytrends.request import TrendReq


def trends(keywords, timeframe="today 3-m"):
    pt = TrendReq(hl="id-ID", tz=420, timeout=(10, 25))
    pt.build_payload(keywords[:5], timeframe=timeframe, geo="ID")  # maks 5 keyword
    df = pt.interest_over_time()
    time.sleep(2)  # jangan spam
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("keywords", nargs="+")
    ap.add_argument("--timeframe", default="today 3-m")
    ap.add_argument("--out", default="trends.csv")
    args = ap.parse_args()

    df = trends(args.keywords, args.timeframe)
    if df.empty:
        print("Tidak ada data (keyword terlalu kecil volumenya).")
    else:
        print(df.drop(columns=["isPartial"], errors="ignore").tail(10))
        df.to_csv(args.out)
        print(f"\nTersimpan: {args.out}")
