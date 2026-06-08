def parse_pubdate(raw):
    """Handle both 'YYYY-MM-DD HH:MM:SS' and RFC822 feed date formats."""
    raw = raw.strip()
    if not raw:
        return None
    # Format the feed actually uses, e.g. '2026-06-08 22:27:17'
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        # Feed times are UTC (no tz info), so attach UTC
        return dt.replace(tzinfo=ZoneInfo("UTC"))
    except ValueError:
        pass
    # Fallback: standard RFC 2822 RSS dates
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        return None


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
