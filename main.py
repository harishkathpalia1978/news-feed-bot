import os
import requests
import xml.etree.ElementTree as ET
import json
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

# ---------- CONFIG (from environment) ----------
RSS_URL = "https://www.investing.com/rss/news.rss"
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
TOPIC_ID = int(os.environ["TOPIC_ID"])      # message_thread_id of the topic
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "10"))
SEEN_FILE = os.environ.get("SEEN_FILE", "seen_articles.json")
# -----------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen), f)
    except Exception as e:
        print(f"Could not save seen file: {e}", flush=True)


def fetch_news():
    resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    news = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="(no title)")
        link = item.findtext("link", default="")
        pub_date_raw = item.findtext("pubDate", default="")

        pub_dt = None
        if pub_date_raw:
            try:
                pub_dt = parsedate_to_datetime(pub_date_raw)
            except Exception:
                pub_dt = None

        news.append({"title": title, "link": link, "pub_dt": pub_dt})
    return news


def is_today(pub_dt):
    if pub_dt is None:
        return False
    today = datetime.now(TZ).date()
    return pub_dt.astimezone(TZ).date() == today


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "message_thread_id": TOPIC_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, data=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def post_daily_news():
    print(f"[{datetime.now(TZ)}] Running daily news job...", flush=True)
    seen = load_seen()

    try:
        articles = fetch_news()
    except Exception as e:
        print(f"Failed to fetch news: {e}", flush=True)
        return

    todays = [a for a in articles if is_today(a["pub_dt"]) and a["link"] not in seen]
    todays = todays[:MAX_ARTICLES]

    if not todays:
        print("No new articles for today.", flush=True)
        return

    date_str = datetime.now(TZ).strftime("%B %d, %Y")
    send_telegram(f"<b>📰 Financial News — {date_str}</b>")

    for a in todays:
        msg = f"<b>{a['title']}</b>\n{a['link']}"
        try:
            send_telegram(msg)
            seen.add(a["link"])
            time.sleep(2)
        except Exception as e:
            print(f"Failed to post '{a['title']}': {e}", flush=True)

    save_seen(seen)
    print(f"Posted {len(todays)} articles.", flush=True)


if __name__ == "__main__":
    post_daily_news()
