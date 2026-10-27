"""Read an existing public annual-report page without writing it to disk."""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from equitylens.live.reader import PublicReader
from equitylens.live.types import Evidence


async def main():
    source = Evidence(
        id="reader-check",
        title="Microsoft Annual Report 2025",
        publisher="Microsoft",
        url="https://www.microsoft.com/investor/reports/ar25/index.html",
        retrieved_at=datetime.now(UTC),
        category="research",
        evidence_level="search_excerpt",
    )
    result = await PublicReader().read(source)
    print({"status": result["status"], "blocks": len(result["blocks"]), "notice": result.get("notice", "")})
    if result["status"] == "read":
        assert any("revenue" in b["text"].lower() for b in result["blocks"])
        assert all(b["locator"].startswith("HTML block") for b in result["blocks"])
        print("Public HTML reading and source locations verified.")


asyncio.run(main())
