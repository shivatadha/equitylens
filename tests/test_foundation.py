import hashlib
import io
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

from equitylens.adapters import download, fetch_sec, validate_url
from equitylens.facts import normalize_companyfacts
from equitylens.ingestion import blob_path, ingest
from equitylens.models import Company, DocumentVersion, FinancialFact, IngestionRun
from equitylens.parser import parse


def company_id(factory, country="US"):
    with factory() as session:
        return session.scalar(select(Company.id).where(Company.country == country))


def import_text(store, content=b"Revenue was INR 100 crore.", key="report", **kwargs):
    factory, settings = store
    return ingest(
        factory,
        settings,
        company_id=company_id(factory),
        source_id="user-upload",
        source_key=key,
        title="Test report",
        content=content,
        kind="text/plain",
        extractor=parse,
        **kwargs,
    )


def fixture_facts(value="100.25", accession="0001-25-000001", cik=789019):
    return json.dumps(
        {
            "cik": cik,
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenue",
                        "units": {
                            "USD": [
                                {
                                    "val": value,
                                    "start": "2024-07-01",
                                    "end": "2025-06-30",
                                    "filed": "2025-07-30",
                                    "accn": accession,
                                    "form": "10-K",
                                    "fy": 2025,
                                    "fp": "FY",
                                }
                            ]
                        },
                    }
                }
            },
        }
    ).encode()


def test_cross_listed_identity_is_one_company(client):
    all_companies = client.get("/companies").json()
    assert len(all_companies) == 2
    companies = client.get("/companies", params={"q": "INFY"}).json()
    assert len(companies) == 1
    assert {x["exchange"] for x in companies[0]["listings"]} == {"NSE", "BSE", "NYSE"}
    assert companies[0]["reporting_currency"] == "INR"


def test_upload_search_and_preserved_bytes(client):
    cid = client.get("/companies", params={"q": "MSFT"}).json()[0]["id"]
    content = b"Annual revenue increased. Cloud revenue is discussed here."
    response = client.post(
        f"/companies/{cid}/imports/upload",
        data={"title": "Annual report", "document_key": "annual-2025"},
        files={"file": ("annual.txt", content, "text/plain")},
    )
    assert response.status_code == 200, response.text
    version = response.json()["version_id"]
    matches = client.get(f"/companies/{cid}/evidence", params={"q": "revenue"}).json()
    assert matches[0]["version_id"] == version
    assert matches[0]["published_on"] is None
    assert matches[0]["publication_basis"] == "unknown"
    assert "chars" in matches[0]["locator"]
    assert client.get(f"/documents/{version}/original").content == content


def test_duplicate_and_revised_versions(store):
    first = import_text(store)
    duplicate = import_text(store)
    revision = import_text(store, b"Revenue was restated to INR 90 crore.")
    assert duplicate["status"] == "duplicate"
    assert duplicate["version_id"] == first["version_id"]
    assert revision["version_id"] != first["version_id"]
    with store[0]() as session:
        versions = session.scalars(select(DocumentVersion)).all()
        assert len(versions) == 2
        assert len({v.document_id for v in versions}) == 1
        for v in versions:
            assert hashlib.sha256(blob_path(store[1], v.sha256).read_bytes()).hexdigest() == v.sha256


def test_company_evidence_does_not_leak(client, store):
    import_text(store)
    indian = company_id(store[0], "IN")
    assert client.get(f"/companies/{indian}/evidence", params={"q": "revenue"}).json() == []


def test_as_of_excludes_unknown_or_later_observed_material(client, store):
    import_text(store, published_on=date(2024, 1, 1), publication_basis="user_provided")
    cid = company_id(store[0])
    assert (
        client.get(f"/companies/{cid}/evidence", params={"q": "revenue", "as_of": "2024-02-01"}).json() == []
    )


def test_failed_parser_is_recorded(store):
    with pytest.raises(UnicodeDecodeError):
        import_text(store, b"\xff\xfe\xfa")
    with store[0]() as session:
        run = session.scalar(select(IngestionRun))
        assert run.status == "failed"
        assert run.error
        assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 0


def test_blank_pdf_is_flagged_for_review(store):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    result = ingest(
        *store,
        company_id=company_id(store[0]),
        source_id="user-upload",
        source_key="scan",
        title="Scan",
        content=stream.getvalue(),
        kind="application/pdf",
        extractor=parse,
    )
    assert result["status"] == "needs_review"
    assert "OCR" in result["warning"]


def test_html_scripts_excluded():
    parsed = parse(
        b"<html><body><main>Revenue 100<script>steal()</script><style>secret</style></main></body></html>",
        "text/html",
    )
    text = " ".join(c["text"] for c in parsed["chunks"])
    assert "Revenue 100" in text
    assert "steal" not in text and "secret" not in text


def test_precision_and_revision_preserved(store):
    data = fixture_facts("12345678901234567890.12345678")
    facts, rejected = normalize_companyfacts(data, "0000789019")
    assert rejected == 0
    assert facts[0]["value"] == Decimal("12345678901234567890.12345678")
    factory, settings = store
    cid = company_id(factory)
    for content in [fixture_facts("100.25"), fixture_facts("95.25", "0001-26-000002")]:
        ingest(
            factory,
            settings,
            company_id=cid,
            source_id="sec-edgar",
            source_key="companyfacts",
            title="SEC facts",
            content=content,
            kind="application/json",
        )
    with factory() as session:
        values = session.scalars(select(FinancialFact.value)).all()
        assert set(values) == {Decimal("100.25"), Decimal("95.25")}


def test_mismatched_sec_identity_rejected():
    with pytest.raises(ValueError, match="identifier"):
        normalize_companyfacts(fixture_facts(), "0001067491")


def test_no_silent_numeric_rounding():
    facts, rejected = normalize_companyfacts(fixture_facts("1.000000001"), "0000789019")
    assert facts == [] and rejected == 1


def test_sec_requires_real_user_agent(store):
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        fetch_sec("0000789019", store[1])


@pytest.mark.parametrize(
    "url",
    [
        "http://www.infosys.com/a",
        "https://127.0.0.1/a",
        "https://www.infosys.com.evil.com/a",
        "https://user:pass@www.infosys.com/a",
        "https://www.infosys.com:8443/a",
    ],
)
def test_unapproved_destinations_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url, {"www.infosys.com"})


def test_redirects_rejected():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        download(
            "https://www.infosys.com/report",
            max_bytes=1000,
            user_agent="test",
            allowed_hosts={"www.infosys.com"},
            transport=transport,
        )


def test_download_size_limit():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"a" * 101))
    with pytest.raises(ValueError, match="limit"):
        download(
            "https://www.infosys.com/report",
            max_bytes=100,
            user_agent="test",
            allowed_hosts={"www.infosys.com"},
            transport=transport,
        )


def test_cross_origin_write_rejected(client):
    response = client.post("/companies/fake/imports/sec", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_wrong_company_catalog_rejected(client):
    cid = client.get("/companies", params={"q": "MSFT"}).json()[0]["id"]
    assert client.post(f"/companies/{cid}/imports/catalog/infosys-annual-2025").status_code == 422


def test_invalid_source_link_and_file_rejected(client):
    cid = client.get("/companies", params={"q": "MSFT"}).json()[0]["id"]
    response = client.post(
        f"/companies/{cid}/imports/upload",
        data={"title": "Unsafe", "document_key": "x", "source_url": "javascript:alert(1)"},
        files={"file": ("x.txt", b"content", "text/plain")},
    )
    assert response.status_code == 422
    response = client.post(
        f"/companies/{cid}/imports/upload",
        data={"title": "Fake PDF", "document_key": "x"},
        files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 422


def test_unknown_company_returns_404(client):
    assert client.get("/companies/missing/documents").status_code == 404
