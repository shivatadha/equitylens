import httpx
import pytest
from fastapi.testclient import TestClient

from equitylens.config import Settings
from equitylens.live.service import ResearchService
from equitylens.live_api import create_app
from equitylens.presentation import price_chart


@pytest.mark.parametrize(
    ("query", "market", "symbol"), [("Demo India", "IN", "DEMO-IN"), ("Demo US", "US", "DEMO-US")]
)
def test_public_demo_runs_research_and_cited_followup_without_external_apis(
    monkeypatch, query, market, symbol
):
    async def no_external_requests(*args, **kwargs):
        pytest.fail("Public sample mode must not contact a data provider or model API")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_external_requests)
    settings = Settings(
        _env_file=None,
        equitylens_data_mode="demo",
        llm_api_key="unused-test-key",
        llm_model="unused-model",
        tavily_api_key="unused-tavily",
        eodhd_api_key="unused-eodhd",
    )
    with TestClient(create_app(settings, ResearchService(settings))) as client:
        assert client.get("/live/capabilities").json()["data_mode"] == "demo"
        assert client.get("/live/companies", params={"q": "RELIANCE.NS"}).json() == []
        match = client.get("/live/companies", params={"q": query, "market": market}).json()[0]
        assert match["symbol"] == symbol
        result = client.post("/live/research", json={"company_token": match["company_token"]})
        assert result.status_code == 200, result.text
        payload = result.json()
        report = payload["report"]
        assert report["data_mode"] == "demo"
        assert report["financials"] and report["analysis"]
        assert report["generation"]["model"] == "sample-extractive"
        assert report["stock"]["quote"]["label"] == "SAMPLE CLOSING PRICE"
        assert report["stock"]["quote"]["timestamp"] is None
        assert all(row["provider"] == "Synthetic demonstration data" for row in report["financials"])
        chart, frame = price_chart(report["stock"]["history"], "30D", "Close", report["stock"]["currency"])
        assert len(chart.to_dict()["vconcat"][0]["layer"][0]["encoding"]["x"]["axis"]["values"]) == len(frame)
        assert "synthetic" in " ".join(report["source_notes"]).lower()
        answer = client.post(
            f"/live/sessions/{payload['session_id']}/follow-up",
            headers={"Authorization": "Bearer " + payload["session_token"]},
            json={"question": "How does the sample company generate operating cash flow?"},
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["matches"] and answer.json()["analysis"]
        assert all(
            claim["source_ids"] for section in answer.json()["analysis"] for claim in section["claims"]
        )
        assert "unused-test-key" not in result.text + answer.text
