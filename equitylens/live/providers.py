"""Bounded, request-time source clients. No provider response is persisted."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from ..config import Settings
from .types import CompanyMatch, Evidence

US_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS", "BATS", "NASDAQ", "NYSE"}
METRICS = {
    "TotalRevenue": "Revenue",
    "OperatingIncome": "Operating income",
    "NetIncome": "Net income",
    "OperatingCashFlow": "Operating cash flow",
    "CapitalExpenditure": "Capital expenditure (reported sign)",
    "TotalDebt": "Total debt",
    "CashCashEquivalentsAndShortTermInvestments": "Cash and short-term investments",
}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def utcnow():
    return datetime.now(UTC)


def safe_link(value):
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            return ""
        return urlunsplit((url.scheme, url.netloc, url.path, url.query, ""))
    except (TypeError, ValueError):
        return ""


async def request(client, method, url, **kwargs):
    # Destinations are fixed in code. Search-result links are never fetched here.
    async with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        body = bytearray()
        async for part in response.aiter_bytes():
            body.extend(part)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Source response exceeds the per-call limit.")
        return bytes(body)


class PublicSources:
    def __init__(self, transport=None, *, settings=None, model_transport=None):
        self.transport = transport
        self.settings = settings or Settings()
        self.model_transport = model_transport

    def client(self):
        return httpx.AsyncClient(
            timeout=httpx.Timeout(22, connect=8),
            follow_redirects=False,
            headers={"User-Agent": "EquityLens/0.2 local research"},
            transport=self.transport,
        )

    async def sec_filings(self, company):
        from .sec import fetch_sec_filings

        return await fetch_sec_filings(company, self.settings.sec_user_agent, self.transport)

    async def nse_announcements(self, company, days):
        from .nse import fetch_nse_announcements

        async with self.client() as client:
            return await fetch_nse_announcements(company, days, client)

    async def lookup(self, query, market="AUTO"):
        async with self.client() as client:
            body = await request(
                client,
                "GET",
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": query, "quotesCount": 12, "newsCount": 0},
            )
        payload = json.loads(body)
        results = []
        seen = set()
        for item in payload.get("quotes", []):
            if item.get("quoteType") != "EQUITY":
                continue
            symbol = item.get("symbol", "")
            exchange = item.get("exchange", "")
            country = "IN" if exchange in {"NSI", "BSE"} else "US" if exchange in US_EXCHANGES else None
            if not country or market not in {"AUTO", country} or symbol in seen:
                continue
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=\-]{0,24}", symbol):
                continue
            seen.add(symbol)
            results.append(
                CompanyMatch(
                    symbol=symbol,
                    name=item.get("longname") or item.get("shortname") or symbol,
                    exchange=item.get("exchDisp") or exchange,
                    market=country,
                    industry=item.get("industryDisp") or item.get("industry") or "",
                    sector=item.get("sectorDisp") or item.get("sector") or "",
                    retrieved_at=utcnow(),
                )
            )
        return results[:8]

    async def financials(self, company):
        now = utcnow()
        types = [f"{period}{metric}" for period in ("annual", "quarterly") for metric in METRICS]
        url = f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{quote(company.symbol, safe='')}"
        async with self.client() as client:
            body = await request(
                client,
                "GET",
                url,
                params={
                    "type": ",".join(types),
                    "period1": int((now - timedelta(days=365 * 6)).timestamp()),
                    "period2": int(now.timestamp()),
                },
            )
        payload = json.loads(body, parse_float=Decimal)
        rows = []
        seen = set()
        for result in payload.get("timeseries", {}).get("result") or []:
            for key in types:
                for item in result.get(key, []):
                    try:
                        raw = Decimal(str(item["reportedValue"]["raw"]))
                        if not raw.is_finite():
                            continue
                        end = datetime.fromisoformat(item["asOfDate"]).date()
                        period = item.get("periodType", "")
                        if end > now.date() or period not in {"12M", "3M"}:
                            continue
                        if key.startswith("annual") and period != "12M":
                            continue
                        if key.startswith("quarterly") and period != "3M":
                            continue
                        metric = key.removeprefix("annual").removeprefix("quarterly")
                        unique = (metric, str(end), period, item["currencyCode"])
                        if unique in seen:
                            continue
                        seen.add(unique)
                        rows.append(
                            {
                                "metric": metric,
                                "label": METRICS[metric],
                                "value": str(raw),
                                "currency": item["currencyCode"],
                                "period_end": str(end),
                                "period_type": period,
                                "source_url": f"https://finance.yahoo.com/quote/{quote(company.symbol, safe='')}/financials/",
                                "retrieved_at": now.isoformat(),
                                "provider": "Yahoo Finance public financial history",
                                "basis": "Provider aggregate; consolidated/standalone basis not independently verified",
                            }
                        )
                    except (KeyError, TypeError, ValueError, InvalidOperation):
                        continue
        return sorted(rows, key=lambda r: (r["period_end"], r["period_type"], r["metric"]), reverse=True)

    async def stock(self, company):
        from .stock import build_stock_snapshot

        now = utcnow()
        symbol = quote(company.symbol, safe="")
        async with self.client() as client:
            chart_task = request(
                client,
                "GET",
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"range": "2y", "interval": "1d", "events": "div,splits"},
            )
            facts_task = request(
                client,
                "GET",
                f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
                params={
                    "type": "trailingDilutedEPS,quarterlyOrdinarySharesNumber,quarterlyStockholdersEquity",
                    "period1": int((now - timedelta(days=365 * 3)).timestamp()),
                    "period2": int(now.timestamp()),
                },
            )
            chart, facts = await asyncio.gather(bounded_call(chart_task, 25), bounded_call(facts_task, 25))
        if chart is None:
            raise ValueError("Stock chart source is unavailable.")
        try:
            fact_data = json.loads(facts, parse_float=Decimal) if facts is not None else {}
            if not isinstance(fact_data, dict):
                fact_data = {}
        except (ValueError, TypeError):
            fact_data = {}
        return build_stock_snapshot(
            company,
            json.loads(chart, parse_float=Decimal),
            fact_data,
            utcnow(),
        )

    async def news(self, company, days, category="news", limit=15):
        # Anchor every query to the resolved company; industry discovery has separate labelling.
        name = re.sub(
            r"\b(incorporated|corporation|limited|ltd|inc|corp|plc)\.?\b", "", company.name, flags=re.I
        ).strip(" ,.")
        name = name.replace('"', "")
        if category == "industry":
            if not company.industry:
                return []
            query = f'"{company.industry}" (industry OR demand OR competition OR regulation) when:{days}d'
        elif category == "projects":
            query = f'"{name}" (project OR factory OR investment OR expansion OR launch OR acquisition) when:{days}d'
        else:
            query = f'"{name}" when:{days}d'
        now = utcnow()
        params = {
            "q": query,
            "hl": "en-IN" if company.market == "IN" else "en-US",
            "gl": "IN" if company.market == "IN" else "US",
            "ceid": "IN:en" if company.market == "IN" else "US:en",
        }
        async with self.client() as client:
            body = await request(client, "GET", "https://news.google.com/rss/search", params=params)
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("Unexpected XML declarations in feed.")
        root = ElementTree.fromstring(body)
        evidence = []
        for item in root.findall("./channel/item"):
            try:
                published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(UTC)
            except (ValueError, TypeError):
                continue
            if not now - timedelta(days=days) <= published <= now + timedelta(minutes=5):
                continue
            title = (item.findtext("title") or "").strip()
            url = safe_link(item.findtext("link"))
            publisher = item.findtext("source") or "Unspecified publisher"
            if not title or not url:
                continue
            evidence.append(
                Evidence(
                    title=title[:500],
                    url=url,
                    publisher=publisher[:200],
                    published_at=published,
                    retrieved_at=now,
                    category=category,
                    evidence_level="headline",
                )
            )
        return sorted(evidence, key=lambda e: e.published_at, reverse=True)[:limit]

    async def web_research(self, company, api_key, depth):
        # One bounded multi-topic search for primary-source discovery; never fetch arbitrary URLs.
        query = f'"{company.name}" {company.symbol} official investor relations latest results projects management guidance risks industry outlook'
        async with self.client() as client:
            body = await request(
                client,
                "POST",
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "query": query,
                    "topic": "general",
                    "search_depth": "advanced" if depth == "detailed" else "basic",
                    "max_results": 8,
                    "include_raw_content": False,
                    "include_answer": False,
                    "include_published_date": True,
                },
            )
        evidence = []
        for item in json.loads(body).get("results", []):
            url = safe_link(item.get("url"))
            if not url:
                continue
            published = None
            try:
                if item.get("published_date"):
                    published = parsedate_to_datetime(item["published_date"]).astimezone(UTC)
            except (ValueError, TypeError):
                try:
                    published = datetime.fromisoformat(item["published_date"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            evidence.append(
                Evidence(
                    title=str(item.get("title", "Source"))[:500],
                    url=url,
                    publisher=urlsplit(url).hostname or "",
                    published_at=published,
                    retrieved_at=utcnow(),
                    category="research",
                    content=str(item.get("content", ""))[:2500],
                    evidence_level="search_excerpt",
                )
            )
        return evidence

    async def profile(self, company, api_key):
        # Demo access is documented for a few symbols only; never substitute one company for another.
        demo = not api_key
        if demo and company.symbol not in {"TSLA", "AAPL", "AMZN"}:
            return None
        token = api_key or "demo"
        symbol = company.symbol
        ticker = (
            symbol.removesuffix(".NS") + ".NSE"
            if symbol.endswith(".NS")
            else symbol.removesuffix(".BO") + ".BSE"
            if symbol.endswith(".BO")
            else symbol + ".US"
        )
        async with self.client() as client:
            body = await request(
                client,
                "GET",
                f"https://eodhd.com/api/fundamentals/{quote(ticker, safe='')}",
                params={"api_token": token, "filter": "General", "fmt": "json"},
            )
        payload = json.loads(body)
        data = payload.get("General", payload)
        if str(data.get("Code", "")).upper() != symbol.split(".")[0]:
            raise ValueError("Provider returned a different company.")
        return {
            "description": str(data.get("Description", ""))[:6000],
            "website": safe_link(data.get("WebURL")),
            "sector": data.get("Sector", ""),
            "industry": data.get("Industry", ""),
            "source_url": "https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds",
            "retrieved_at": utcnow().isoformat(),
            "provider": "EODHD documented demo" if demo else "EODHD API",
        }

    async def synthesize(self, company, evidence, metrics, model, question=None):
        from .llm import APIModel

        return await APIModel(self.settings, self.model_transport).generate(
            company, evidence, metrics, model, question
        )

    async def model_status(self, model="", *, check=False):
        from .llm import APIModel

        return await APIModel(self.settings, self.model_transport).status(model, check=check)


async def bounded_call(coro, seconds=35):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except (
        httpx.HTTPError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ElementTree.ParseError,
        TimeoutError,
    ):
        # Never expose upstream URLs: some providers put credentials in query parameters.
        return None
