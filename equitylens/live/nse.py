"""Official NSE announcement summaries from its public, rolling RSS feed."""

import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from .providers import request, safe_link, utcnow
from .types import Evidence

FEED_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
ARCHIVE_HOSTS = {"nsearchives.nseindia.com", "archives.nseindia.com"}
INDIA_TIME = ZoneInfo("Asia/Kolkata")


def parse_announcements(body, company, days, *, now=None):
    """Keep only dated, linked items whose archive filename starts with the exact NSE ticker."""
    if company.market != "IN" or not company.symbol.endswith(".NS"):
        return []
    ticker = company.symbol.removesuffix(".NS").upper()
    now = now or utcnow()
    root = ElementTree.fromstring(body)
    items = []
    seen = set()
    for item in root.findall("./channel/item"):
        link = safe_link(item.findtext("link"))
        parsed = urlsplit(link)
        filename = unquote(PurePosixPath(parsed.path).name).upper()
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ARCHIVE_HOSTS
            or not filename.startswith(ticker + "_")
            or link in seen
        ):
            continue
        try:
            published = datetime.strptime(item.findtext("pubDate", ""), "%d-%b-%Y %H:%M:%S").replace(
                tzinfo=INDIA_TIME
            )
        except ValueError:
            continue
        if not now - timedelta(days=days) <= published.astimezone(now.tzinfo) <= now:
            continue
        seen.add(link)
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item.findtext("description", ""))).strip()
        title = summary.split("|SUBJECT:", 1)[0].strip() or item.findtext("title", "NSE announcement")
        items.append(
            Evidence(
                title=title[:500],
                url=link,
                publisher="National Stock Exchange of India",
                published_at=published,
                retrieved_at=now,
                category="official_announcement",
                categories=["official_announcement"],
                content=(summary or title)[:1500],
                evidence_level="official_feed_summary",
                locator="NSE announcements RSS summary; linked document may not have been read",
            )
        )
    return sorted(items, key=lambda entry: entry.published_at, reverse=True)[:8]


async def fetch_nse_announcements(company, days, client):
    if company.market != "IN" or not company.symbol.endswith(".NS"):
        return []
    body = await request(client, "GET", FEED_URL)
    return parse_announcements(body, company, days)
