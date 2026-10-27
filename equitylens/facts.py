"""Conservative SEC fact ingestion: preserve taxonomy, accession, period and units.

No cross-concept mapping, currency conversion, or annual/quarter inference occurs here.
"""

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation


def normalize_companyfacts(data: bytes, expected_cik: str):
    payload = json.loads(data, parse_float=Decimal)
    if str(payload.get("cik", "")).zfill(10) != expected_cik:
        raise ValueError("SEC company identifier does not match the selected company.")
    rows = []
    rejected = 0
    seen = set()
    # Retain selected reported concepts; don't add together overlapping revenue tags.
    selected = {
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "NetIncomeLoss",
        "ProfitLoss",
        "OperatingIncomeLoss",
        "Assets",
        "Liabilities",
        "StockholdersEquity",
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CashAndCashEquivalentsAtCarryingValue",
        "EarningsPerShareDiluted",
        "Revenue",
        "ProfitLossFromOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "Equity",
    }
    for taxonomy, concepts in payload.get("facts", {}).items():
        if taxonomy not in {"us-gaap", "ifrs-full"}:
            continue
        for concept, details in concepts.items():
            if concept not in selected:
                continue
            for unit, observations in details.get("units", {}).items():
                for index, item in enumerate(observations):
                    try:
                        value = Decimal(str(item["val"]))
                        if not value.is_finite() or abs(value) >= Decimal("1e30"):
                            raise ValueError("Invalid numeric range")
                        if value != value.quantize(Decimal("0.00000001")):
                            raise ValueError("Precision beyond supported scale")
                        end = date.fromisoformat(item["end"])
                        start = date.fromisoformat(item["start"]) if item.get("start") else None
                        filed = date.fromisoformat(item["filed"])
                        if start and start > end:
                            raise ValueError("Invalid period")
                        accession = item["accn"]
                        form = item["form"]
                        identity = [
                            expected_cik,
                            taxonomy,
                            concept,
                            unit,
                            str(start),
                            str(end),
                            accession,
                            str(value),
                        ]
                        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append(
                            {
                                "identity_hash": key,
                                "taxonomy": taxonomy,
                                "concept": concept,
                                "label": details.get("label", concept)[:300],
                                "value": value,
                                "unit": unit,
                                "period_start": start,
                                "period_end": end,
                                "filed_on": filed,
                                "form": form,
                                "accession": accession,
                                "fiscal_year": item.get("fy"),
                                "fiscal_period": item.get("fp"),
                                "locator": f"/facts/{taxonomy}/{concept}/units/{unit}/{index}",
                                "metadata_json": {
                                    "frame": item.get("frame"),
                                    "basis": "SEC entity-wide fact; reporting basis requires filing review",
                                    "original_value": str(value),
                                },
                            }
                        )
                    except (KeyError, ValueError, TypeError, InvalidOperation):
                        rejected += 1
    return rows, rejected
