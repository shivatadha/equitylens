"""Exercise the running API and the actual Streamlit application with real sources."""

import json
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

root = Path(__file__).resolve().parents[1]
api = "http://127.0.0.1:8001"
report = {"api": {}, "ui": {}}
with httpx.Client(timeout=30) as client:
    response = client.get(api + "/health")
    response.raise_for_status()
    report["api"]["health"] = response.json()
    companies = client.get(api + "/companies").json()
    for company in companies:
        cid = company["id"]
        documents = client.get(api + f"/companies/{cid}/documents").json()
        result = client.get(api + f"/companies/{cid}/evidence", params={"q": "revenue"})
        result.raise_for_status()
        evidence = result.json()
        assert documents, f"No real source imported for {company['name']}"
        assert evidence, f"No revenue evidence found for {company['name']}"
        assert all(item["version_id"] in {v["id"] for v in documents} for item in evidence)
        report["api"][company["name"]] = {
            "versions": len(documents),
            "search_matches": len(evidence),
            "sample_locator": evidence[0]["locator"],
        }
    assert client.get("http://127.0.0.1:8501/_stcore/health").status_code == 200

app = AppTest.from_file(str(root / "ui.py"), default_timeout=30)
app.run()
assert not app.exception, str(app.exception)
report["ui"]["initial_render"] = "passed"
app.text_input[0].set_value("revenue").run()
assert not app.exception, str(app.exception)
report["ui"]["evidence_search"] = "passed"
current = app.selectbox[0].value
other = next(c for c in companies if c["id"] != current["id"])
app.selectbox[0].set_value(other).run()
assert not app.exception, str(app.exception)
report["ui"]["switch_company_with_results"] = "passed"
path = root / "data" / "verification.json"
path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
