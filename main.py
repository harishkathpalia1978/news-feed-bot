from datetime import datetime, timedelta

def is_recent(pub_dt, hours=24):
    if pub_dt is None:
        return False
    cutoff = datetime.now(TZ) - timedelta(hours=hours)
    return pub_dt.astimezone(TZ) >= cutoff


def post_daily_news():
    print(f"[{datetime.now(TZ)}] Running daily news job...", flush=True)
    seen = load_seen()

    try:
        articles = fetch_news()
    except Exception as e:
        print(f"Failed to fetch news: {e}", flush=True)
        return

    # --- TEMPORARY DEBUG ---
    print(f"Fetched {len(articles)} articles total.", flush=True)
    for a in articles[:5]:
        print(f"   pub_dt={a['pub_dt']}  title={a['title'][:60]}", flush=True)
    print(f"Now in {TZ}: {datetime.now(TZ)}", flush=True)
    # --- END DEBUG ---

    todays = [a for a in articles if is_recent(a["pub_dt"]) and a["link"] not in seen]
    todays = todays[:MAX_ARTICLES]

    if not todays:
        print("No new articles in window.", flush=True)
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
