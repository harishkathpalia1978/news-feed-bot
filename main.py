import os
import sys
import time
import re
from datetime import datetime
from zoneinfo import ZoneInfo

print(">>> SCRIPT STARTED", flush=True)

import requests
from bs4 import BeautifulSoup

# ---------- CONFIG (from environment) ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
TOPIC_ID = os.environ.get("TOPIC_ID")  # optional; None/empty/"1" => no thread
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "30"))
DEBUG = os.environ.get("DEBUG", "1") == "1"

US_COUNTRY_ID = "5"          # Investing.com country id for United States
WANTED_IMPORTANCE = ["2", "3"]  # 2 = medium (2 stars), 3 = high (3 stars)
# -----------------------------------------------

CAL_URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"

print(f">>> Config: BOT_TOKEN set={bool(BOT_TOKEN)}, "
      f"CHANNEL_ID={CHANNEL_ID}, TOPIC_ID={TOPIC_ID}, TZ={TZ}", flush=True)

if not BOT_TOKEN or not CHANNEL_ID:
    print("!!! MISSING REQUIRED ENV VARS (BOT_TOKEN and/or CHANNEL_ID). Exiting.", flush=True)
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.investing.com/economic-calendar/",
    "Origin": "https://www.investing.com",
}


def fetch_calendar_html():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    print(f">>> Fetching US calendar for {today}...", flush=True)

    # Build form body. country[] and importance[] are repeated keys.
    payload = [
        ("country[]", US_COUNTRY_ID),
        ("dateFrom", today),
        ("dateTo", today),
        ("timeZone", "8"),       # 8 = GMT-5 (US Eastern-ish); adjust if times look off
        ("timeFilter", "timeOnly"),
        ("currentTab", "custom"),
        ("limit_from", "0"),
    ]
    for imp in WANTED_IMPORTANCE:
        payload.append(("importance[]", imp))

    resp = requests.post(CAL_URL, headers=HEADERS, data=payload, timeout=20)
    print(f">>> HTTP status: {resp.status_code}, body length: {len(resp.text)} chars", flush=True)
    resp.raise_for_status()

    # The endpoint returns JSON with a 'data' field containing HTML rows,
    # OR raw HTML depending on version. Handle both.
    try:
        j = resp.json()
        html = j.get("data", "")
        print(">>> Parsed JSON response; using 'data' field.", flush=True)
    except ValueError:
        html = resp.text
        print(">>> Response was raw HTML (not JSON).", flush=True)

    return html


def parse_events(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr", id=re.compile(r"eventRowId_\d+"))
    print(f">>> Found {len(rows)} event rows in HTML.", flush=True)

    events = []
    for row in rows:
        # Time
        t_cell = row.find("td", class_=re.compile("time"))
        ev_time = t_cell.get_text(strip=True) if t_cell else ""

        # Importance: count filled bull/star icons via the sentiment cell
        imp_cell = row.find("td", class_=re.compile("sentiment"))
        stars = 0
        if imp_cell:
            stars = len(imp_cell.find_all("i", class_=re.compile("grayFullBullishIcon")))
            # title attr like "Moderate Volatility Expected" / "High Volatility Expected"
            title = imp_cell.get("title", "")
        else:
            title = ""

        # Event name
        name_cell = row.find("td", class_=re.compile("event"))
        name = name_cell.get_text(strip=True) if name_cell else "(event)"

        # Actual / Forecast / Previous
        def cell_text(cls):
            c = row.find("td", class_=re.compile(cls))
            return c.get_text(strip=True) if c else ""

        actual = cell_text("act")
        forecast = cell_text("fore")
        previous = cell_text("prev")

        events.append({
            "time": ev_time,
            "stars": stars,
            "title": title,
            "name": name,
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
        })

    if DEBUG:
        print(">>> Sample parsed events:", flush=True)
        for e in events[:10]:
            print(f"      {e['time']}  stars={e['stars']}  {e['name'][:45]}", flush=True)

    return events[:MAX_EVENTS]


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if TOPIC_ID and TOPIC_ID not in ("1", ""):
        payload["message_thread_id"] = int(TOPIC_ID)

    resp = requests.post(url, data=payload, timeout=15)
    if resp.status_code != 200:
        print(f">>> Telegram error {resp.status_code}: {resp.text}", flush=True)
    resp.raise_for_status()
    return resp.json()


def star_icon(stars):
    if stars >= 3:
        return "🔴"
    if stars == 2:
        return "🟠"
    return "⚪"


def post_calendar():
    print(f">>> [{datetime.now(TZ)}] Running US economic calendar job...", flush=True)

    try:
        html = fetch_calendar_html()
    except Exception as e:
        print(f"!!! Failed to fetch calendar: {e}", flush=True)
        return

    try:
        events = parse_events(html)
    except Exception as e:
        print(f"!!! Failed to parse events: {e}", flush=True)
        return

    date_str = datetime.now(TZ).strftime("%A, %B %d, %Y")

    if not events:
        print(">>> No US high/medium events today.", flush=True)
        send_telegram(f"<b>🇺🇸 US Economic Calendar — {date_str}</b>\n\n"
                      f"No high or medium importance events scheduled today.")
        return

    lines = [f"<b>🇺🇸 US Economic Calendar — {date_str}</b>", ""]
    for e in events:
        icon = star_icon(e["stars"])
        t_part = f"<code>{e['time']}</code> " if e["time"] else ""
        line = f"{icon} {t_part}{e['name']}"

        extras = []
        if e["forecast"]:
            extras.append(f"f/c {e['forecast']}")
        if e["previous"]:
            extras.append(f"prev {e['previous']}")
        if extras:
            line += f"  <i>({', '.join(extras)})</i>"

        lines.append(line)

    message = "\n".join(lines)

    try:
        if len(message) <= 4000:
            send_telegram(message)
        else:
            chunk, length = [], 0
            for line in lines:
                if length + len(line) > 3500:
                    send_telegram("\n".join(chunk))
                    time.sleep(1)
                    chunk, length = [], 0
                chunk.append(line)
                length += len(line) + 1
            if chunk:
                send_telegram("\n".join(chunk))
        print(f">>> Posted {len(events)} events.", flush=True)
    except Exception as e:
        print(f"!!! Failed to post: {e}", flush=True)


if __name__ == "__main__":
    post_calendar()
    print(">>> SCRIPT FINISHED", flush=True)
