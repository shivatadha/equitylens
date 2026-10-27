# Optional frontier-model API answers

EquityLens generates answers through a remote model API. Generation is disabled until both an API key and model ID are configured. Live prices, financial history, news, calculations and evidence retrieval work without a model API.

## Enable when needed

Edit the project's ignored `.env` file. Start from `.env.example`; keep real keys out of Git.

```text
LLM_API_KEY=your-provider-api-key
LLM_MODEL=your-provider-model-id
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_API_MODE=responses
LLM_MAX_OUTPUT_TOKENS=4096
LLM_TIMEOUT_SECONDS=90
LLM_REASONING_EFFORT=
```

Replace the first two placeholders with your credentials and exact model ID. Choose a model available to your account that supports the configured API protocol and strict JSON-schema output. The application does not assume which frontier model your account can access and does not choose a cheaper substitute or fallback model.

Restart the API after editing `.env`:

```powershell
.\.venv\Scripts\python.exe -m equitylens.cli serve
```

API settings are managed in `.env`, with keys kept on the server. There are no API credential or model controls in the browser toolbar. The optional `/live/model?check=true` diagnostic endpoint checks model metadata only when explicitly requested. It does not generate an answer or prove structured-output support or sufficient billing quota. Startup and ordinary configuration-status requests do not contact the model provider.

Then run fresh company research to generate the outlook, or ask a question in **Outlook & questions**. These actions can incur your provider's API charges once configured.

If a report has no outlook, its `generation.failure_code` and `generation.message` distinguish provider rate limits, authentication, rejected requests, timeouts, invalid responses, and claims withheld by evidence checks. The UI shows the safe message. Existing reports created before this diagnostic change do not have those fields; run fresh research to see the reason.

## Protocol choices

- `responses` is the default: POST `<LLM_API_BASE_URL>/responses`, using `text.format` with a strict JSON schema.
- `chat_completions` supports a compatible gateway: POST `<LLM_API_BASE_URL>/chat/completions`, using `response_format.json_schema` with `strict: true`. Set your gateway's public HTTPS base URL and its exact model ID.

The Chat Completions gateway must support bearer authorization, strict JSON schema, `max_completion_tokens`, `store: false`, and non-streaming responses. Native provider APIs with different payloads are not automatically compatible. Unsupported features produce an explicit failure with source passages retained; the app does not silently weaken citation requirements or retry through another provider.

Groq's compatible Chat Completions endpoint was checked with `openai/gpt-oss-20b` on 27 October 2026:

```text
LLM_API_BASE_URL=https://api.groq.com/openai/v1
LLM_API_MODE=chat_completions
LLM_MODEL=openai/gpt-oss-20b
```

Set `LLM_API_KEY` locally to a valid Groq key. One structured-output request with fictional evidence succeeded. The `/live/model?check=true` diagnostic also accepts providers that expose a model-list endpoint but no single-model endpoint. Model availability and provider behavior can change.

Leave `LLM_REASONING_EFFORT` blank unless your selected model supports it. When explicitly set, it is sent as `reasoning.effort` for Responses or `reasoning_effort` for Chat Completions. The output-token budget includes provider reasoning tokens where applicable; raise it if a reasoning model repeatedly returns incomplete responses. Incomplete, refused, invalid or truncated output is not displayed as a completed answer.

Official references: [Responses and structured output](https://developers.openai.com/api/docs/guides/structured-outputs), [model metadata](https://developers.openai.com/api/reference/resources/models/methods/retrieve).

## Evidence and privacy

Only retrieved passage text, passage IDs, evidence levels, relevant deterministic calculations, the company name, and the specific question are sent for answer generation. The complete report corpus, embedding vectors and API key are not included in the prompt. The key travels only in the authorization header. Requests disable provider response storage with `store: false`; this does not guarantee zero retention of provider logs. The provider's own policies still apply.

The local embedding encoder remains a separate retrieval component; it does not write answers or require an answer-model API key. Application research text and vectors retain the existing session-memory expiry and clear behavior. Answer generation has no local inference runtime or local-model fallback.

Clear `LLM_API_KEY` or `LLM_MODEL` and restart the API to disable generation. Retrieval-only follow-ups remain available. No real model key is included in this repository. A small, optional [answer evaluation pilot](EVALUATION.md) exercises Groq against frozen, public-filing facts; it does not establish live-source answer accuracy. Controlled API-response tests cover both protocols, missing configuration, secret handling, citations and failure behavior.

Older `OLLAMA_*` configuration and `ollama_model` request fields have been retired; requests now use the provider-neutral `llm_model` field.
