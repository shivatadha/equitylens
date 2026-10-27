"""Narrow source adapters. No arbitrary URL fetch endpoint is exposed."""

import threading
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class PublicDocument:
    cik: str
    title: str
    url: str
    period_end: date | None = None


CATALOG = {
    "microsoft-annual-2025": PublicDocument(
        "0000789019",
        "Microsoft Annual Report 2025",
        "https://www.microsoft.com/investor/reports/ar25/index.html",
        date(2025, 6, 30),
    ),
    "infosys-annual-2025": PublicDocument(
        "0001067491",
        "Infosys Integrated Annual Report 2024–25",
        "https://www.infosys.com/investors/reports-filings/annual-report/annual/documents/infosys-ar-25.pdf",
        date(2025, 3, 31),
    ),
    "infosys-financial-overview": PublicDocument(
        "0001067491",
        "Infosys three-year financial overview",
        "https://www.infosys.com/investors/reports-filings/financials/data-sheet.html",
    ),
}

_sec_lock = threading.Lock()
_sec_last = 0.0


def validate_url(url, allowed_hosts):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("This destination is not permitted by the source adapter.")


def download(url, *, max_bytes, user_agent, allowed_hosts, transport=None):
    validate_url(url, allowed_hosts)
    with httpx.Client(
        timeout=httpx.Timeout(45, connect=15), follow_redirects=False, transport=transport
    ) as client:
        with client.stream("GET", url, headers={"User-Agent": user_agent, "Accept": "*/*"}) as response:
            # Redirects are intentionally rejected: never forward to an unreviewed destination.
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"Source returned HTTP {response.status_code}.")
            content = bytearray()
            for part in response.iter_bytes():
                content.extend(part)
                if len(content) > max_bytes:
                    raise ValueError("Source document exceeds the import limit.")
            return bytes(content), response.headers.get("content-type", "").split(";")[0]


def fetch_public(key, settings):
    if key not in CATALOG:
        raise ValueError("Unknown curated document.")
    item = CATALOG[key]
    content, media = download(
        item.url,
        max_bytes=settings.max_upload_bytes,
        user_agent="EquityLens/0.1 local company research",
        allowed_hosts={"www.microsoft.com", "www.infosys.com"},
    )
    return item, content, media


def fetch_sec(cik, settings):
    if not (len(cik) == 10 and cik.isdigit()):
        raise ValueError("A ten-digit SEC company identifier is required.")
    if "@" not in settings.sec_user_agent:
        raise ValueError(
            "Set SEC_USER_AGENT in .env to your project name and real contact email before SEC imports."
        )
    global _sec_last
    # Single-process local service. Distributed limiter is required before multi-worker deployment.
    with _sec_lock:
        time.sleep(max(0, 0.25 - (time.monotonic() - _sec_last)))
        _sec_last = time.monotonic()
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        content, _ = download(
            url,
            max_bytes=settings.max_upload_bytes,
            user_agent=settings.sec_user_agent,
            allowed_hosts={"data.sec.gov"},
        )
    return url, content
