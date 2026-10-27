import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from equitylens.config import Settings
from equitylens.live.llm import APIModel
from equitylens.live.providers import PublicSources
from equitylens.live.rag import SessionIndex, chunk_blocks
from equitylens.live.service import ResearchService
from equitylens.live.types import CompanyMatch, Evidence, FollowUpInput
from equitylens.live_api import create_app

KEY = "test-secret-not-a-real-key"
MODEL = "frontier-example"


def settings(**kwargs):
    return Settings(database_url="", _env_file=None, **kwargs)


def configured(**kwargs):
    return settings(llm_api_key=KEY, llm_model=MODEL, **kwargs)


def company():
    return CompanyMatch(
        symbol="ACME", name="Acme", exchange="NASDAQ", market="US", retrieved_at=datetime.now(UTC)
    )


def passage():
    return Evidence(
        id="F1:C001",
        title="Cash flow",
        url="https://example.com",
        publisher="Example",
        retrieved_at=datetime.now(UTC),
        category="financial",
        content="Operating cash flow was USD 100 for the reported period.",
        evidence_level="structured_fact",
        locator="Observation F1",
    )


def answer():
    return {
        "sections": [
            {
                "title": "Answer",
                "claims": [
                    {"text": "The supplied observation reports cash generation.", "source_ids": ["F1:C001"]}
                ],
            }
        ]
    }


def reply(mode, result=None):
    content = json.dumps(answer() if result is None else result)
    if mode == "responses":
        return {
            "status": "completed",
            "output": [
                {"type": "reasoning"},
                {"type": "message", "content": [{"type": "output_text", "text": content}]},
            ],
        }
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


def never_connect(request):
    raise AssertionError("This operation must not contact the model API.")


@pytest.mark.parametrize("config", [settings(), settings(llm_model=MODEL), settings(llm_api_key=KEY)])
def test_missing_key_or_model_never_connects_even_on_check_or_generation(config):
    model = APIModel(config, httpx.MockTransport(never_connect))
    assert asyncio.run(model.status(check=True))["state"] in {"disabled", "missing_model"}
    assert asyncio.run(model.generate(company(), [passage()], [])) == {"sections": []}


def test_model_override_cannot_enable_api_without_a_key():
    model = APIModel(settings(), httpx.MockTransport(never_connect))
    assert asyncio.run(model.generate(company(), [passage()], [], MODEL)) == {"sections": []}


def test_configured_model_does_not_probe_until_requested():
    model = APIModel(configured(), httpx.MockTransport(never_connect))
    assert asyncio.run(model.status())["state"] == "configured"


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_generation_uses_remote_bounded_structured_payload_with_retrieved_passage_ids(mode):
    captured = []

    def respond(request):
        assert request.url.host == "api.openai.com"
        assert request.url.path == "/v1/" + ("responses" if mode == "responses" else "chat/completions")
        assert request.headers["Authorization"] == "Bearer " + KEY
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=reply(mode))

    config = configured(llm_api_mode=mode)
    provider = PublicSources(settings=config, model_transport=httpx.MockTransport(respond))
    result = asyncio.run(
        provider.synthesize(company(), [passage()], [], MODEL, question="What was operating cash flow?")
    )
    assert result == answer()
    payload = captured[0]
    assert payload["model"] == MODEL
    assert payload["stream"] is False and payload["store"] is False
    assert "temperature" not in payload and "tools" not in payload
    assert KEY not in json.dumps(payload)
    if mode == "responses":
        assert payload["max_output_tokens"] == 4096
        assert "reasoning" not in payload
        schema = payload["text"]["format"]["schema"]
        supplied = json.loads(payload["input"])
    else:
        assert payload["max_completion_tokens"] == 4096
        assert "reasoning_effort" not in payload
        schema = payload["response_format"]["json_schema"]["schema"]
        supplied = json.loads(payload["messages"][1]["content"])
    ids = schema["properties"]["sections"]["items"]["properties"]["claims"]["items"]["properties"][
        "source_ids"
    ]["items"]["enum"]
    assert ids == ["F1:C001"]
    assert [p["id"] for p in supplied["evidence"]] == ["F1:C001"]
    assert supplied["specific_question"] == "What was operating cash flow?"


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_reasoning_parameter_only_sent_if_explicitly_configured(mode):
    def respond(request):
        payload = json.loads(request.content)
        if mode == "responses":
            assert payload["reasoning"] == {"effort": "low"}
        else:
            assert payload["reasoning_effort"] == "low"
        return httpx.Response(200, json=reply(mode))

    model = APIModel(configured(llm_api_mode=mode, llm_reasoning_effort="low"), httpx.MockTransport(respond))
    assert asyncio.run(model.generate(company(), [passage()], [])) == answer()


@pytest.mark.parametrize(
    ("mode", "response"),
    [
        (
            "responses",
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
        ),
        (
            "responses",
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "Declined"}]}],
            },
        ),
        ("chat_completions", {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}),
        (
            "chat_completions",
            {"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]},
        ),
        ("chat_completions", {"choices": []}),
    ],
)
def test_truncated_refused_or_empty_results_are_not_accepted(mode, response):
    model = APIModel(
        configured(llm_api_mode=mode), httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    )
    with pytest.raises(ValueError):
        asyncio.run(model.generate(company(), [passage()], []))


def test_invalid_json_structure_is_rejected():
    model = APIModel(
        configured(), httpx.MockTransport(lambda request: httpx.Response(200, json=reply("responses", [])))
    )
    with pytest.raises(ValueError, match="invalid research structure"):
        asyncio.run(model.generate(company(), [passage()], []))


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://192.168.1.1/v1",
        "https://user:secret@api.example.com/v1",
        "https://api.example.com/v1?key=secret",
        "https://api.example.com/v1#secret",
    ],
)
def test_model_api_requires_https_and_rejects_private_or_credential_urls(url):
    with pytest.raises(ValidationError):
        settings(llm_api_base_url=url)


def test_compatible_gateway_uses_configured_base_and_encoded_model_path():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.host == "gateway.example.com"
        assert request.url.raw_path == b"/api/v1/models/vendor%2Ffrontier"
        return httpx.Response(200, json={"id": "vendor/frontier"})

    model = APIModel(
        configured(llm_api_base_url="https://gateway.example.com/api/v1/", llm_api_mode="chat_completions"),
        httpx.MockTransport(respond),
    )
    assert asyncio.run(model.status("vendor/frontier", check=True))["state"] == "ready"
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.parametrize(
    ("code", "state"),
    [
        (401, "unauthorized"),
        (403, "unauthorized"),
        (404, "unavailable"),
        (429, "rate_limited"),
        (503, "unavailable"),
    ],
)
def test_diagnostics_explain_api_failures_without_exposing_secrets(code, state):
    model = APIModel(configured(), httpx.MockTransport(lambda request: httpx.Response(code, text=KEY)))
    result = asyncio.run(model.status(check=True))
    assert result["state"] == state
    assert KEY not in json.dumps(result)


def test_diagnostics_accept_provider_with_model_list_only():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path.startswith("/v1/models/"):
            return httpx.Response(404)
        return httpx.Response(200, json={"data": [{"id": MODEL}]})

    model = APIModel(configured(), httpx.MockTransport(respond))
    result = asyncio.run(model.status(check=True))
    assert result["state"] == "ready"
    assert paths == ["/v1/models/" + MODEL, "/v1/models"]


def test_status_redirect_does_not_forward_api_key_to_another_host():
    calls = []

    def respond(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://elsewhere.example/model"})

    model = APIModel(configured(), httpx.MockTransport(respond))
    assert asyncio.run(model.status(check=True))["state"] == "unavailable"
    assert calls == ["api.openai.com"]


def test_no_evidence_makes_no_generation_request():
    model = APIModel(configured(), httpx.MockTransport(never_connect))
    assert asyncio.run(model.generate(company(), [], [])) == {"sections": []}


def test_default_capabilities_and_status_are_secret_free_and_disabled():
    config = settings()
    provider = PublicSources(settings=config, model_transport=httpx.MockTransport(never_connect))
    with TestClient(create_app(config, ResearchService(config, provider))) as client:
        capabilities = client.get("/live/capabilities").json()
        assert capabilities["generation_model"] is None
        assert capabilities["generation_configured"] is False
        assert "local_model_configured" not in capabilities
        assert client.get("/live/model", params={"check": True}).json()["state"] == "disabled"


def test_configured_startup_and_capability_checks_make_no_remote_calls_or_secret_disclosures():
    config = configured()
    assert KEY not in repr(config)
    provider = PublicSources(settings=config, model_transport=httpx.MockTransport(never_connect))
    with TestClient(create_app(config, ResearchService(config, provider))) as client:
        capabilities = client.get("/live/capabilities").json()
        assert capabilities["generation_configured"] is True
        status = client.get("/live/model").json()
        assert status["state"] == "configured"
        assert KEY not in json.dumps([status, capabilities])


def test_invalid_model_ids_and_retired_local_model_input_are_rejected():
    with pytest.raises(ValidationError):
        FollowUpInput(question="Test question", llm_model="../models?key=secret")
    with pytest.raises(ValidationError):
        FollowUpInput(question="Test question", ollama_model="old-local-name")


@pytest.mark.parametrize("scenario", ["disabled", "success", "unauthorized", "rate_limited"])
def test_followup_uses_api_or_keeps_retrieved_evidence_without_leaking_credentials(scenario):
    calls = []

    def respond(request):
        calls.append(request)
        assert scenario != "disabled"
        if scenario == "success":
            return httpx.Response(200, json=reply("responses"))
        return httpx.Response(429 if scenario == "rate_limited" else 401, text=KEY)

    class OfflineEncoder:
        model_name = "unused"

        def documents(self, texts):
            raise OSError("Use the real keyword retrieval path for this API boundary test.")

    config = settings() if scenario == "disabled" else configured()
    provider = PublicSources(settings=config, model_transport=httpx.MockTransport(respond))
    service = ResearchService(config, provider)
    source = passage().model_copy(update={"id": "F1"})
    index = SessionIndex(
        chunk_blocks(source, [{"text": source.content, "locator": "Observation F1"}]), OfflineEncoder()
    )
    sid, secret = service.sessions.create(
        {"company": company().model_dump(mode="json"), "calculations": []}, index
    )
    with TestClient(create_app(config, service)) as client:
        response = client.post(
            f"/live/sessions/{sid}/follow-up",
            headers={"Authorization": "Bearer " + secret},
            json={"question": "What was operating cash flow?", "llm_model": MODEL},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["matches"][0]["id"] == "F1:C001"
        assert bool(result["analysis"]) == (scenario == "success")
        assert bool(calls) == (scenario != "disabled")
        assert KEY not in response.text
        assert "local model" not in result["notice"].lower()
        if scenario == "unauthorized":
            assert result["failure_code"] == "authentication"
        elif scenario == "rate_limited":
            assert result["failure_code"] == "rate_limit"
            assert "rate limit" in result["notice"]
        else:
            assert result["failure_code"] is None
