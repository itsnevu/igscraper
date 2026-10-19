"""
Statistik channel YouTube kompetitor via YouTube Data API v3 (gratis, 10.000 unit/hari).

API key gratis: console.cloud.google.com -> buat project -> aktifkan
"YouTube Data API v3" -> Credentials -> API key. Tidak perlu billing.

Usage:
    export YT_API_KEY="..."
    python youtube_competitor.py @nike UCxxxxxxxxxxxx
"""
import os
import sys

from googleapiclient.discovery import build

API_KEY = os.getenv("YT_API_KEY", "")


def client():
    if not API_KEY:
        sys.exit("Set dulu: export YT_API_KEY=\"...\" (cara buat ada di atas file ini)")
    return build("youtube", "v3", developerKey=API_KEY)


def channel_stats(yt, channel):
    # terima channel ID (UC...) atau handle (@nama)
    kw = {"forHandle": channel} if channel.startswith("@") else {"id": channel}
    return yt.channels().list(part="statistics,snippet", **kw).execute()


def recent_videos(yt, channel_id, max_results=10):
    # search.list = 100 unit; cukup untuk puluhan channel per hari
    return yt.search().list(part="snippet", channelId=channel_id, type="video",
                            order="date", maxResults=max_results).execute()


def video_stats(yt, video_ids):
    return yt.videos().list(part="statistics", id=",".join(video_ids)).execute()


if __name__ == "__main__":
    yt = client()
    for ch in sys.argv[1:]:
        res = channel_stats(yt, ch).get("items", [])
        if not res:
            print(f"{ch}: tidak ditemukan"); continue
        c = res[0]; s = c["statistics"]
        print(f"{c['snippet']['title']}: subs={s.get('subscriberCount')} "
              f"views={s.get('viewCount')} videos={s.get('videoCount')}")
        vids = recent_videos(yt, c["id"], 5).get("items", [])
        ids = [v["id"]["videoId"] for v in vids]
        stats = {v["id"]: v["statistics"] for v in video_stats(yt, ids).get("items", [])} if ids else {}
        for v in vids:
            st = stats.get(v["id"]["videoId"], {})
            print(f"  {v['snippet']['publishedAt'][:10]} views={st.get('viewCount')} "
                  f"likes={st.get('likeCount')} | {v['snippet']['title'][:60]}")
