import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

print(">>> SCRIPT STARTED", flush=True)

import requests
from ecocal import Calendar

# ---------- CONFIG (from environment) ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
TOPIC_ID = os.environ.get("TOPIC_ID")  # optional; None/empty/"1" => no thread
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
MAX_EVENTS = int(os.environ.get("MAX_EVENTS", "30"))
# Importance levels to include: high + medium
WANTED_IMPORTANCE = {"high", "medium", "moderate"}
# -----------------------------------------------

print(f">>> Config: BOT_TOKEN set={bool(BOT_TOKEN)}, "
      f"CHANNEL_ID={CHANNEL_ID}, TOPIC_ID={TOPIC_ID}, TZ={TZ}", flush=True)

if not BOT_TOKEN or not CHANNEL_ID:
    print("!!! MISSING REQUIRED ENV VARS (BOT_TOKEN and/or CHANNEL_ID). Exiting.", flush=True)
    sys.exit(1)


def fetch_events():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    print(f">>> Fetching economic calendar for {today}...", flush=True)

    cal = Calendar(
        startHorizon=today,
        endHorizon=today,
        withDetails=True,
        nbThreads=10,
    )
    df = cal.getCalendar() if hasattr(cal, "getCalendar") else cal.calendar
    print(f">>> Calendar returned {len(df)} total events.", flush=True)
    print(f">>> Available columns: {list(df.columns)}", flush=True)
    return df


def normalize(val):
    return str(val).strip().lower() if val is not None else ""


def filter_us(df):
    cols = {c.lower(): c for c in df.columns}

    # Find the country/currency column and the importance column flexibly
    country_col = next((cols[c] for c in cols
                        if c in ("country", "zone", "currency", "ccy")), None)
    imp_col = next((cols[c] for c in cols
                   if "importance" in c or "impact" in c or "volatility" in c), None)
    name_col = next((cols[c] for c in cols
                    if c in ("event", "name", "title", "indicator")), None)
    time_col = next((cols[c] for c in cols
                    if "time" in c or "date" in c), None)

    print(f">>> Using columns -> country={country_col}, importance={imp_col}, "
          f"name={name_col}, time={time_col}", flush=True)

    events = []
    for _, row in df.iterrows():
        country = normalize(row.get(country_col)) if country_col else ""
        importance = normalize(row.get(imp_col)) if imp_col else ""

        is_us = any(tok in country for tok in ("united states", "usa", "us", "usd"))
        is_wanted = any(level in importance for level in WANTED_IMPORTANCE)

        if is_us and is_wanted:
            events.append({
                "name": row.get(name_col, "(event)") if name_col else "(event)",
                "time": row.get(time_col, "") if time_col else "",
                "importance": importance,
            })

    print(f">>> {len(events)} US high+medium events after filtering.", flush=True)
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


def importance_icon(imp):
    if "high" in imp:
        return "🔴"
    if "medium" in imp or "moderate" in imp:
        return "🟠"
    return "⚪"


def post_calendar():
    print(f">>> [{datetime.now(TZ)}] Running economic calendar job...", flush=True)

    try:
        df = fetch_events()
    except Exception as e:
        print(f"!!! Failed to fetch calendar: {e}", flush=True)
        return

    try:
        events = filter_us(df)
    except Exception as e:
        print(f"!!! Failed to filter events: {e}", flush=True)
        return

    date_str = datetime.now(TZ).strftime("%A, %B %d, %Y")

    if not events:
        print(">>> No US high/medium events today. Posting 'nothing scheduled' note.", flush=True)
        send_telegram(f"<b>🇺🇸 US Economic Calendar — {date_str}</b>\n\n"
                      f"No high or medium importance events scheduled today.")
        return

    lines = [f"<b>🇺🇸 US Economic Calendar — {date_str}</b>", ""]
    for e in events:
        icon = importance_icon(normalize(e["importance"]))
        t = str(e["time"]).strip()
        t_part = f"<code>{t}</code> " if t else ""
        lines.append(f"{icon} {t_part}{e['name']}")

    message = "\n".join(lines)

    # Telegram caps messages at ~4096 chars; split if needed
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
