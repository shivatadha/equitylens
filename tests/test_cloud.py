from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_cloud_entrypoint_requires_password_and_opens_embedded_app(monkeypatch):
    monkeypatch.setenv("EQUITYLENS_DEMO_PASSWORD", "local-cloud-test-password")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("EQUITYLENS_BACKEND", "test")
    monkeypatch.setenv("EQUITYLENS_REQUIRE_DEMO_PASSWORD", "0")
    monkeypatch.setenv("RAG_DOWNLOAD_MODEL_IF_MISSING", "0")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "cloud_app.py", default_timeout=10).run()
    assert len(app.text_input) == 1
    assert not app.exception

    app.text_input[0].set_value("wrong")
    app.button[0].click()
    app.run()
    assert any("Incorrect demo password" in str(error.value) for error in app.error)

    app.text_input[0].set_value("local-cloud-test-password")
    app.button[0].click()
    app.run()
    assert app.session_state["demo_authenticated"] is True
    assert any(title.value == "Company research" for title in app.title)
    assert not app.exception
