"""Live checks without saving company data or generated reports to disk."""

import time
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

API = "http://127.0.0.1:8001"
sessions = []
with httpx.Client(timeout=140) as client:
    assert client.get(API + "/health").json()["mode"] == "on-demand"
    capabilities = client.get(API + "/live/capabilities").json()
    for name, market, symbol, currency in [
        ("Tesla", "US", "TSLA", "USD"),
        ("Reliance", "IN", "RELIANCE.NS", "INR"),
    ]:
        found = client.get(API + "/live/companies", params={"q": name, "market": market})
        found.raise_for_status()
        match = next(c for c in found.json() if c["symbol"] == symbol)
        response = client.post(
            API + "/live/research", json={"company_token": match["company_token"], "news_days": 7}
        )
        response.raise_for_status()
        payload = response.json()
        report = payload["report"]
        assert report["company"]["symbol"] == symbol
        stock = report["stock"]
        assert stock and stock["currency"] == currency, report["gaps"]
        assert stock["quote"]["timestamp"] and stock["quote"]["price"]
        assert len(stock["history"]) > 200
        assert len(stock["performance"]) >= 3
        assert any(row["id"] == "pe_ttm" for row in stock["valuation"])
        assert any(source["id"] == "M1" for source in report["rag_sources"])
        assert report["financials"], report["gaps"]
        assert any(r["currency"] == currency for r in report["financials"])
        news = [e for e in report["evidence"] if "news" in e["categories"]]
        assert news, report["gaps"]
        print(
            {
                "company": symbol,
                "stock_currency": stock["currency"],
                "daily_bars": len(stock["history"]),
                "valuation_metrics": len(stock["valuation"]),
                "quote_as_of": stock["quote"]["exchange_timestamp"],
                "financial_observations": len(report["financials"]),
                "news_results": len(news),
                "projects": sum("projects" in e["categories"] for e in report["evidence"]),
                "industry": sum("industry" in e["categories"] for e in report["evidence"]),
                "retention_minutes": payload["expires_in_seconds"] // 60,
            },
            flush=True,
        )
        assert report["rag"]["mode"] == "hybrid_semantic_bm25", report["rag"]
        assert report["rag"]["dimensions"] == 384
        assert report["rag"]["passages"] > 0
        retrieval = client.post(
            API + "/live/sessions/" + payload["session_id"] + "/retrieve",
            headers={"Authorization": "Bearer " + payload["session_token"]},
            json={
                "question": "How much cash does the company generate from operating activities?",
                "top_k": 6,
            },
        )
        retrieval.raise_for_status()
        passages = retrieval.json()["passages"]
        assert passages
        assert any(p["evidence_level"] == "structured_fact" for p in passages)
        assert all(p["source_id"] and p["locator"] for p in passages)
        stock_hits = client.post(
            API + "/live/sessions/" + payload["session_id"] + "/retrieve",
            headers={"Authorization": "Bearer " + payload["session_token"]},
            json={"question": "What is the stock price and trailing P/E valuation?", "top_k": 6},
        )
        stock_hits.raise_for_status()
        assert any(p["category"] == "stock" for p in stock_hits.json()["passages"])
        print(
            {
                "rag_mode": report["rag"]["mode"],
                "passages": report["rag"]["passages"],
                "retrieval_hits": len(passages),
                "sources_read": report["rag"]["body_sources_read"],
            },
            flush=True,
        )
        sessions.append(payload)

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "ui.py"), default_timeout=40)
    app.session_state["research_id"] = sessions[0]["session_id"]
    app.session_state["research_token"] = sessions[0]["session_token"]
    app.session_state["expires_at"] = time.time() + 1800
    app.run()
    assert not app.exception, str(app.exception)
    assert any(metric.label == "Last regular-session price" for metric in app.metric)
    assert app.tabs[0].label == "Stock & price"
    assert not any(tab.label == "Overview" for tab in app.tabs)
    assert not any(field.label == "News window" for field in app.sidebar.selectbox)
    assert not app.sidebar.button
    assert not any(field.label == "API model ID" for field in app.text_input)
    assert not any("Ollama" in field.label for field in app.text_input)
    if not capabilities.get("generation_configured"):
        assert any("AI outlook is optional" in message.value for message in app.info)
    next(x for x in app.selectbox if x.label == "Chart period").set_value("30D").run()
    next(x for x in app.selectbox if x.label == "Price basis").set_value("Provider adjusted close").run()
    assert not app.exception, str(app.exception)
    for window in ["90D", "1Y", "2Y"]:
        next(x for x in app.selectbox if x.label == "Chart period").set_value(window).run()
        assert not app.exception, str(app.exception)
    assert any("Holding period" in table.value.columns for table in app.dataframe)
    assert any("Stock measure" in table.value.columns for table in app.dataframe)
    next(x for x in app.selectbox if x.label == "Reporting period").set_value("Quarterly").run()
    assert not app.exception, str(app.exception)
    financial_display = next(
        table.value for table in app.dataframe if "Financial measure" in table.value.columns
    )
    assert set(financial_display["Reporting period"]) == {"Quarter · 3 months"}
    app.session_state["research_id"] = sessions[1]["session_id"]
    app.session_state["research_token"] = sessions[1]["session_token"]
    app.run()
    next(x for x in app.selectbox if x.label == "Amount display").set_value("Full amounts").run()
    assert not app.exception, str(app.exception)
    rupees = next(table.value for table in app.dataframe if "Financial measure" in table.value.columns)
    assert all("₹" in value and "E+" not in value for value in rupees["Amount"])
    print(
        "Interface: first stock tab, all four chart ranges, meaningful tables and Indian currency formatting passed.",
        flush=True,
    )
    print("Interface: live report, financial table, charts and evidence tabs rendered.", flush=True)
    next(x for x in app.text_input if x.label == "Search passages by meaning").set_value(
        "How is cash generated?"
    )
    next(x for x in app.button if x.label == "Retrieve passages").click().run()
    assert not app.exception, str(app.exception)
    assert app.session_state["retrieval_result"]["passages"]
    print("Interface: semantic passage retrieval passed.", flush=True)
    if not capabilities.get("generation_configured"):
        next(field for field in app.text_input if field.label == "Ask about this session’s evidence").set_value(
            "What is the stock price and trailing P/E?"
        )
        next(button for button in app.button if button.label == "Find relevant evidence").click().run()
        assert not app.exception, str(app.exception)
        assert app.session_state["followup_result"]["matches"]
        assert app.session_state["followup_result"]["analysis"] == []
        print("Interface: retrieval-only follow-up passed with no model call.", flush=True)
    next(x for x in app.text_input if x.label == "Company name or ticker").set_value("Reliance")
    next(x for x in app.button if x.label == "Find company").click().run()
    assert not app.exception, str(app.exception)
    assert app.session_state["matches"]
    print("Interface: dynamic company search passed.", flush=True)
    next(x for x in app.button if x.label == "Clear current research").click().run()
    assert not app.exception, str(app.exception)
    assert "research_id" not in app.session_state
    print("Interface: clear session passed.", flush=True)
    for session in sessions:
        client.delete(
            API + "/live/sessions/" + session["session_id"],
            headers={"Authorization": "Bearer " + session["session_token"]},
        )
