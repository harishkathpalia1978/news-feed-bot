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
# Set DEBUG_ROWS=0 in Railway variables to silence the per-row sample dump
DEBUG_ROWS = os.environ.get("DEBUG_ROWS", "1") == "1"
# -----------------------------------------------

print(f">>> Config: BOT_TOKEN set={bool(BOT_TOKEN)}, "
      f"CHANNEL_ID={CHANNEL_ID}, TOPIC_ID={TOPIC_ID}, TZ={TZ}", flush=True)

if not BOT_TOKEN or not CHANNEL_ID:
    print("!!! MISSING REQUIRED ENV VARS (BOT_TOKEN and/or CHANNEL_ID). Exiting.", flush=True)
    sys.exit(1)


def normalize(val):
    return str(val).strip().lower() if val is not None else ""


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


def filter_us(df):
    # One-time sample dump so we can see the real values if anything looks off
    if DEBUG_ROWS:
        print(">>> Sample rows (countryCode / currencyCode / Impact / volatility / Name):", flush=True)
        for _, row in df.head(10).iterrows():
            print(f"      countryCode={row.get('countryCode')!r}  "
                  f"currencyCode={row.get('currencyCode')!r}  "
                  f"Impact={row.get('Impact')!r}  "
                  f"volatility={row.get('volatility')!r}  "
                  f"Name={str(row.get('Name'))[:40]!r}", flush=True)

    events = []
    for _, row in df.iterrows():
        country = normalize(row.get("countryCode"))    # e.g. 'us'
        currency = normalize(row.get("currencyCode"))  # e.g. 'usd'
        impact = normalize(row.get("Impact"))
        volatility = normalize(row.get("volatility"))

        is_us = country in ("us", "usa") or currency == "usd"

        # Accept either Impact or volatility; handle word labels OR numeric (3=high, 2=medium)
        level = f"{impact} {volatility}"
        is_high = any(w in level for w in ("high", "3"))
        is_medium = any(w in level for w in ("medium", "moderate", "2"))
        is_wanted = is_high or is_medium

        if is_us and is_wanted:
            events.append({
                "name": row.get("Name", "(event)"),
                "time": row.get("Start", "") or row.get("dateUtc", ""),
                "level": level,
                "actual": row.get("actual", ""),
                "consensus": row.get("consensus", ""),
                "previous": row.get("previous", ""),
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


def importance_icon(level):
    level = normalize(level)
    if "high" in level or "3" in level:
        return "🔴"
    if "medium" in level or "moderate" in level or "2" in level:
        return "🟠"
    return "⚪"


def fmt_time(raw):
    """Try to show just HH:MM in the user's timezone; fall back to raw string."""
    if not raw:
        return ""
    s = str(raw)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(TZ).strftime("%H:%M")
        except ValueError:
            continue
    return s  # unknown format: show as-is


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
        icon = importance_icon(e["level"])
        t = fmt_time(e["time"])
        t_part = f"<code>{t}</code> " if t else ""
        line = f"{icon} {t_part}{e['name']}"

        # Append forecast/previous if present (useful at a glance)
        extras = []
        if str(e.get("consensus", "")).strip():
            extras.append(f"f/c {e['consensus']}")
        if str(e.get("previous", "")).strip():
            extras.append(f"prev {e['previous']}")
        if extras:
            line += f"  <i>({', '.join(extras)})</i>"

        lines.append(line)

    message = "\n".join(lines)

    try:
        if len(message) <= 4000:
            send_telegram(message)
        else:
            # Split into chunks under Telegram's ~4096 char limit
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
