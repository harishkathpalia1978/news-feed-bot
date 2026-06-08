import os
import sys
import requests
import xml.etree.ElementTree as ET
import json
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

print(">>> SCRIPT STARTED", flush=True)

# ---------- CONFIG (from environment) ----------
RSS_URL = "https://www.investing.com/rss/news.rss"
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
TOPIC_ID = os.environ.get("TOPIC_ID")  # optional; None/empty/"1" => no thread
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "10"))
SEEN_FILE = os.environ.get("SEEN_FILE", "seen_articles.json")
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "24"))
# -----------------------------------------------

print(f">>> Config: BOT_TOKEN set={bool(BOT_TOKEN)}, "
      f"CHANNEL_ID={CHANNEL_ID}, TOPIC_ID={TOPIC_ID}, "
      f"TZ={TZ}, MAX_ARTICLES={MAX_ARTICLES}, WINDOW_HOURS={WINDOW_HOURS}", flush=True)

if not BOT_TOKEN or not CHANNEL_ID:
    print("!!! MISSING REQUIRED ENV VARS (BOT_TOKEN and/or CHANNEL_ID). "
          "Set them in Railway > Variables. Exiting.", flush=True)
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}


def parse_pubdate(raw):
    """Handle both 'YYYY-MM-DD HH:MM:SS' and RFC822 feed date formats."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=ZoneInfo("UTC"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        return None


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                data = set(json.load(f))
                print(f">>> Loaded {len(data)} seen articles from {SEEN_FILE}", flush=True)
                return data
        except Exception as e:
            print(f">>> Could not read seen file ({e}); starting empty.", flush=True)
            return set()
    print(f">>> No seen file at {SEEN_FILE}; starting empty.", flush=True)
    return set()


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen), f)
        print(f">>> Saved {len(seen)} seen articles.", flush=True)
    except Exception as e:
        print(f">>> Could not save seen file: {e}", flush=True)


def fetch_news():
    print(f">>> Fetching feed: {RSS_URL}", flush=True)
    resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    print(f">>> Feed HTTP status: {resp.status_code}, body length: {len(resp.content)} bytes", flush=True)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    items = root.findall(".//item")
    print(f">>> Feed contained {len(items)} <item> entries.", flush=True)

    news = []
    for item in items:
        title = item.findtext("title", default="(no title)")
        link = item.findtext("link", default="")
        pub_date_raw = item.findtext("pubDate", default="")
        pub_dt = parse_pubdate(pub_date_raw)
        news.append({"title": title, "link": link, "pub_dt": pub_dt})
    return news


def is_recent(pub_dt, hours):
    if pub_dt is None:
        return False
    cutoff = datetime.now(TZ) - timedelta(hours=hours)
    return pub_dt.astimezone(TZ) >= cutoff


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if TOPIC_ID and TOPIC_ID not in ("1", ""):
        payload["message_thread_id"] = int(TOPIC_ID)

    resp = requests.post(url, data=payload, timeout=15)
    if resp.status_code != 200:
        print(f">>> Telegram error {resp.status_code}: {resp.text}", flush=True)
    resp.raise_for_status()
    return resp.json()


def post_daily_news():
    print(f">>> [{datetime.now(TZ)}] Running daily news job...", flush=True)
    seen = load_seen()

    try:
        articles = fetch_news()
    except Exception as e:
        print(f"!!! Failed to fetch news: {e}", flush=True)
        return

    print(f">>> Fetched {len(articles)} articles total. Sample of newest:", flush=True)
    for a in articles[:5]:
        print(f"      pub_dt={a['pub_dt']}  title={a['title'][:60]}", flush=True)
    print(f">>> Now in {TZ}: {datetime.now(TZ)}; window = last {WINDOW_HOURS}h", flush=True)

    recent = [a for a in articles if is_recent(a["pub_dt"], WINDOW_HOURS) and a["link"] not in seen]
    print(f">>> {len(recent)} articles pass the recency+unseen filter.", flush=True)
    recent = recent[:MAX_ARTICLES]

    if not recent:
        print(">>> Nothing to post. Exiting.", flush=True)
        return

    date_str = datetime.now(TZ).strftime("%B %d, %Y")
    print(f">>> Posting header + {len(recent)} articles...", flush=True)
    send_telegram(f"<b>📰 Financial News — {date_str}</b>")

    posted = 0
    for a in recent:
        msg = f"<b>{a['title']}</b>\n{a['link']}"
        try:
            send_telegram(msg)
            seen.add(a["link"])
            posted += 1
            print(f">>> Posted: {a['title'][:60]}", flush=True)
            time.sleep(2)
        except Exception as e:
            print(f"!!! Failed to post '{a['title'][:40]}': {e}", flush=True)

    save_seen(seen)
    print(f">>> Done. Posted {posted} of {len(recent)} articles.", flush=True)


if __name__ == "__main__":
    post_daily_news()
    print(">>> SCRIPT FINISHED", flush=True)
