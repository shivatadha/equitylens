import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from equitylens.live.nse import FEED_URL, fetch_nse_announcements, parse_announcements
from equitylens.live.types import CompanyMatch


def company(symbol="RELIANCE.NS"):
    return CompanyMatch(
        symbol=symbol,
        name="Reliance Industries Limited",
        exchange="NSE",
        market="IN",
        retrieved_at=datetime.now(UTC),
    )


def item(title, filename, published, description="Issuer informed the exchange about a new project."):
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<link>https://nsearchives.nseindia.com/corporate/{filename}</link>"
        f"<pubDate>{published}</pubDate>"
        f"<description>{description}</description>"
        "</item>"
    )


def test_exact_nse_ticker_and_date_prevent_name_collisions():
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    today = "27-Oct-2026 11:00:00"
    old = (now - timedelta(days=12)).astimezone().strftime("%d-%b-%Y %H:%M:%S")
    body = (
        "<rss><channel>"
        + item("Reliance Industries", "RELIANCE_27102026_project.pdf", today)
        + item("Reliance fund", "NIPPON_27102026_nav.pdf", today)
        + item("Reliance Industries", "RELIANCEPOWER_27102026_notice.pdf", today)
        + item("Reliance Industries", "RELIANCE_15102026_old.pdf", old)
        + "</channel></rss>"
    ).encode()
    found = parse_announcements(body, company(), 7, now=now)
    assert len(found) == 1
    assert found[0].evidence_level == "official_feed_summary"
    assert found[0].publisher == "National Stock Exchange of India"
    assert found[0].url.endswith("RELIANCE_27102026_project.pdf")


def test_nse_feed_is_fetched_only_for_nse_listing():
    calls = []
    now = datetime.now(UTC).astimezone().strftime("%d-%b-%Y %H:%M:%S")
    body = ("<rss><channel>" + item("Reliance Industries", "RELIANCE_announcement.pdf", now) + "</channel></rss>").encode()

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=body)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            found = await fetch_nse_announcements(company(), 7, client)
            skipped = await fetch_nse_announcements(company("RELIANCE.BO"), 7, client)
        return found, skipped

    found, skipped = asyncio.run(run())
    assert len(found) == 1
    assert skipped == []
    assert calls == [FEED_URL]
