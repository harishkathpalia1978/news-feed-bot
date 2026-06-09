import os
import sys
import re
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

print(">>> SCRIPT STARTED", flush=True)

import requests

# ---------- CONFIG (from environment) ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
TOPIC_ID = os.environ.get("TOPIC_ID")  # optional; None/empty/"1" => no thread
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "40"))
DEBUG = os.environ.get("DEBUG", "1") == "1"
# -----------------------------------------------

CAL_URL = "https://www.investing.com/economic-calendar/"

print(f">>> Config: BOT_TOKEN set={bool(BOT_TOKEN)}, "
      f"CHANNEL_ID={CHANNEL_ID}, TOPIC_ID={TOPIC_ID}, TZ={TZ}", flush=True)

if not BOT_TOKEN or not CHANNEL_ID:
    print("!!! MISSING REQUIRED ENV VARS (BOT_TOKEN and/or CHANNEL_ID). Exiting.", flush=True)
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_events():
    print(f">>> Fetching {CAL_URL} ...", flush=True)
    r = requests.get(CAL_URL, headers=HEADERS, timeout=25)
    print(f">>> status: {r.status_code}, length: {len(r.text)}", flush=True)
    r.raise_for_status()

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if not m:
        raise RuntimeError("__NEXT_DATA__ blob not found in page.")

    data = json.loads(m.group(1))
    found = []
    _walk_for_events(data, found)
    print(f">>> Extracted {len(found)} total event objects.", flush=True)
    return found


def _walk_for_events(obj, found):
    """Recursively collect dicts that have both 'event' and 'importance' keys."""
    if isinstance(obj, dict):
        if "event" in obj and "importance" in obj and "country" in obj:
            found.append(obj)
        for v in obj.values():
            _walk_for_events(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_for_events(v, found)


def collect_us_events(raw):
    events = []
    seen = set()
    for o in raw:
        country = str(o.get("country", "")).strip().lower()
        importance = str(o.get("importance", "")).strip()
        ev_type = str(o.get("type", "")).strip().lower()

        if country not in ("united states", "usa", "us"):
            continue
        if importance not in ("2", "3"):       # 2 = medium, 3 = high
            continue
        if ev_type == "holiday":                 # skip holidays
            continue

        name = o.get("event", "(event)")
        key = (name, o.get("time", ""))
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "name": name,
            "time": o.get("time", ""),
            "level": int(importance),
            "forecast": str(o.get("forecast", "") or ""),
            "previous": str(o.get("previous", "") or ""),
            "actual": str(o.get("actual", "") or ""),
        })

    if DEBUG:
        print(">>> US medium/high events:", flush=True)
        for e in events[:20]:
            print(f"      lvl={e['level']}  {e['time']}  {e['name'][:45]}", flush=True)

    return events[:MAX_EVENTS]


def fmt_time(iso):
    """Convert ISO UTC timestamp to HH:MM in TZ; fall back to raw."""
    if not iso:
        return ""
    s = str(iso).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(TZ).strftime("%H:%M")
    except ValueError:
        return str(iso)


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


def icon(level):
    return "🔴" if level >= 3 else "🟠"


def post_calendar():
    print(f">>> [{datetime.now(TZ)}] Running US economic calendar job...", flush=True)

    try:
        raw = fetch_events()
        events = collect_us_events(raw)
    except Exception as e:
        print(f"!!! Failed: {e}", flush=True)
        return

    date_str = datetime.now(TZ).strftime("%A, %B %d, %Y")

    if not events:
        print(">>> No US medium/high events today.", flush=True)
        send_telegram(f"<b>🇺🇸 US Economic Calendar — {date_str}</b>\n\n"
                      f"No high or medium importance events scheduled today.")
        return

    lines = [f"<b>🇺🇸 US Economic Calendar — {date_str}</b>", ""]
    for e in events:
        t = fmt_time(e["time"])
        t_part = f"<code>{t}</code> " if t else ""
        line = f"{icon(e['level'])} {t_part}{e['name']}"
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
                    send_telegram("\n".join(chunk)); time.sleep(1)
                    chunk, length = [], 0
                chunk.append(line); length += len(line) + 1
            if chunk:
                send_telegram("\n".join(chunk))
        print(f">>> Posted {len(events)} events.", flush=True)
    except Exception as e:
        print(f"!!! Failed to post: {e}", flush=True)


if __name__ == "__main__":
    post_calendar()
    print(">>> SCRIPT FINISHED", flush=True)
