"""Synthetic portfolio fixtures. Nothing here describes an actual company."""

import math
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from .stock import build_stock_snapshot
from .types import CompanyMatch, Evidence

SOURCE = "https://github.com/shivatadha/equitylens/blob/main/docs/DEMO-DATA.md"
CATALOG = {
    "DEMO-IN": ("EquityLens Demo India Ltd", "IN", "INR", "NSE (fictional)", "Industrial software"),
    "DEMO-US": ("EquityLens Demo US Inc.", "US", "USD", "NASDAQ (fictional)", "Cloud infrastructure"),
}


class DemoEmbedder:
    model_name = "demo-keyword-only"

    def documents(self, texts):
        raise OSError("The public demo uses keyword retrieval without downloading an embedding model.")


class DemoSources:
    """Use the production calculations and retrieval with explicitly fictional inputs."""

    async def lookup(self, query, market="AUTO"):
        words = query.lower().replace("-", " ").split()
        return [
            CompanyMatch(
                symbol=symbol,
                name=name,
                exchange=exchange,
                market=country,
                industry=industry,
                sector="Technology (sample)",
                provider="Synthetic portfolio fixture",
                retrieved_at=datetime.now(UTC),
            )
            for symbol, (name, country, _, exchange, industry) in CATALOG.items()
            if market in {"AUTO", country}
            and all(word in (symbol + " " + name).lower().replace("-", " ") for word in words)
        ]

    async def financials(self, company):
        now = datetime.now(UTC)
        currency = CATALOG[company.symbol][2]
        month = 3 if company.market == "IN" else 6
        year = now.year if now.month > month else now.year - 1
        initial = Decimal("100000000000") if currency == "INR" else Decimal("1000000000")
        factors = {
            "TotalRevenue": ("Revenue", "1"),
            "OperatingIncome": ("Operating income", ".18"),
            "NetIncome": ("Net income", ".12"),
            "OperatingCashFlow": ("Operating cash flow", ".23"),
            "CapitalExpenditure": ("Capital expenditure", "-.07"),
            "TotalDebt": ("Total debt", ".35"),
            "CashCashEquivalentsAndShortTermInvestments": ("Cash and short-term investments", ".15"),
        }
        rows = []
        for offset in range(4):
            end = f"{year - offset}-{month:02d}-{'31' if month == 3 else '30'}"
            revenue = initial * Decimal("1.14") ** (3 - offset)
            for metric, (label, factor) in factors.items():
                rows.append(
                    {
                        "metric": metric,
                        "label": label,
                        "value": str((revenue * Decimal(factor)).quantize(Decimal(".01"))),
                        "currency": currency,
                        "period_end": end,
                        "period_type": "12M",
                        "source_url": SOURCE,
                        "retrieved_at": now.isoformat(),
                        "provider": "Synthetic demonstration data",
                        "basis": "Fictional company; generated sample figures, not reported financial statements",
                    }
                )
        return rows

    async def stock(self, company):
        now = datetime.now(UTC)
        currency = CATALOG[company.symbol][2]
        dates, day = [], now.date() - timedelta(days=1)
        while len(dates) < 500:
            if day.weekday() < 5:
                dates.append(day)
            day -= timedelta(days=1)
        dates.reverse()
        base = 850 if currency == "INR" else 90
        closes = [
            round(base * (1 + i * 0.0006 + 0.035 * math.sin(i / 9) + 0.012 * math.sin(i / 2.7)), 2)
            for i in range(len(dates))
        ]
        opens = [round(value * (0.997 + 0.003 * math.sin(i)), 2) for i, value in enumerate(closes)]
        highs = [round(max(a, b) * 1.012, 2) for a, b in zip(opens, closes, strict=True)]
        lows = [round(min(a, b) * 0.988, 2) for a, b in zip(opens, closes, strict=True)]
        volumes = [1200000 + (i * 7919) % 1800000 for i in range(len(dates))]
        period = (await self.financials(company))[0]["period_end"]
        facts = {
            "timeseries": {
                "result": [
                    {
                        metric: [
                            {
                                "asOfDate": period,
                                "periodType": kind,
                                "currencyCode": currency,
                                "reportedValue": {"raw": value},
                            }
                        ]
                    }
                    for metric, kind, value in [
                        ("trailingDilutedEPS", "TTM", "42.50" if currency == "INR" else "5.25"),
                        ("quarterlyOrdinarySharesNumber", "3M", "100000000"),
                        ("quarterlyStockholdersEquity", "3M", "9000000000"),
                    ]
                ]
            }
        }
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": company.symbol,
                            "currency": currency,
                            "exchangeTimezoneName": "Asia/Kolkata"
                            if currency == "INR"
                            else "America/New_York",
                            "fullExchangeName": company.exchange,
                            "regularMarketPrice": closes[-1],
                            "regularMarketPreviousClose": closes[-2],
                            "regularMarketVolume": volumes[-1],
                            "fiftyTwoWeekHigh": max(highs[-260:]),
                            "fiftyTwoWeekLow": min(lows[-260:]),
                        },
                        "timestamp": [int(datetime.combine(day, time(12), UTC).timestamp()) for day in dates],
                        "indicators": {
                            "quote": [
                                {
                                    "open": opens,
                                    "high": highs,
                                    "low": lows,
                                    "close": closes,
                                    "volume": volumes,
                                }
                            ],
                            "adjclose": [{"adjclose": closes}],
                        },
                    }
                ]
            }
        }
        result = build_stock_snapshot(company, payload, facts, now)
        result.update(
            provider="Synthetic demonstration data",
            source_url=SOURCE,
            data_mode="demo",
            gaps=["All prices and financial inputs are generated examples for a fictional company."],
            notes=[
                "Sample weekdays do not reproduce an actual exchange holiday calendar.",
                "The price series is deterministic and synthetic; no market-data service was contacted.",
            ],
        )
        result["quote"].update(
            label="SAMPLE CLOSING PRICE",
            as_of_date=str(dates[-1]),
            timestamp=None,
            exchange_timestamp=None,
            previous_close_date=str(dates[-2]),
            open=str(opens[-1]),
            day_high=str(highs[-1]),
            day_low=str(lows[-1]),
            basis="Synthetic sample closing price; not an actual security or live quote.",
            delay="Generated sample data; no market-data timestamp or feed.",
        )
        for value in result["valuation"]:
            value["method"] = "Synthetic sample inputs. " + value["method"]
        return result

    async def news(self, company, days, category="news", limit=15):
        now = datetime.now(UTC)
        scenarios = {
            "news": [
                (
                    "Sample scenario: customer subscriptions expand",
                    "The fictional company added subscription customers in this generated demonstration scenario.",
                ),
                (
                    "Sample scenario: operating costs rise",
                    "The fictional company faces higher support costs in this sample scenario.",
                ),
            ],
            "projects": [
                (
                    "Sample scenario: a new product pilot",
                    "This fictional pilot illustrates project discovery. It is not a company announcement.",
                )
            ],
            "industry": [
                (
                    "Sample scenario: competition increases",
                    "This generated industry scenario illustrates a risk to pricing and customer retention.",
                )
            ],
        }
        return [
            Evidence(
                title=title,
                content=content,
                publisher="Synthetic demonstration scenario",
                url=f"{SOURCE}#{category}-{i}",
                published_at=now - timedelta(hours=i + 2),
                retrieved_at=now,
                category=category,
                evidence_level="demo_scenario",
            )
            for i, (title, content) in enumerate(scenarios.get(category, []), 1)
        ][:limit]

    async def profile(self, company, api_key):
        return {
            "description": "A fictional technology company created to demonstrate EquityLens. All associated prices, financials, and developments are synthetic.",
            "website": "",
            "source_url": SOURCE,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "provider": "Synthetic demonstration profile",
        }

    async def web_research(self, *args):
        return []

    async def sec_filings(self, company):
        return {"facts": [], "evidence": []}

    async def nse_announcements(self, company, days):
        return []

    async def synthesize(self, company, evidence, metrics, model, question=None):
        if not evidence:
            return {"sections": []}
        terms = set((question or "operating cash flow revenue").lower().split())
        selected = max(
            evidence,
            key=lambda item: sum(term in (item.title + " " + item.content).lower() for term in terms),
        )
        return {
            "sections": [
                {
                    "title": "Extractive sample answer",
                    "claims": [
                        {"text": "Synthetic demo evidence: " + selected.content, "source_ids": [selected.id]}
                    ],
                }
            ]
        }

    async def model_status(self, model="", *, check=False):
        return {
            "state": "demo",
            "model": "sample-extractive",
            "message": "Copies retrieved sample evidence with citations. No model API is called.",
        }
