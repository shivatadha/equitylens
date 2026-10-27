# Streamlit Community Cloud deployment

Use `cloud_app.py` as the entrypoint when you are ready to publish EquityLens from a GitHub repository. It runs the existing live FastAPI app **inside the Streamlit process** on a dedicated event loop, so no separate API service or Docker Compose deployment is needed. The API routes and temporary bearer-protected research sessions remain the same. This entrypoint requires an invitation password before it calls the embedded API.

## Repository preparation

Push the project files to GitHub with `cloud_app.py`, `ui.py`, `equitylens/`, `assets/`, `requirements.txt`, and `packages.txt` at the repository root. `.env`, `.streamlit/secrets.toml`, `data/`, `.cache/`, and `.venv/` must stay out of Git. The root `requirements.txt` is the Community Cloud dependency file; `packages.txt` installs the Linux `libgomp1` library used by the embedding runtime. Select **Python 3.12** during deployment.

The cloud app downloads the general `BAAI/bge-small-en-v1.5` embedding weights on first use if they are absent. The weights live in the instance cache; company text and vectors remain in expiring memory. If the download or model load fails, the app labels retrieval as keyword-only. Cold starts may be slow, and a Community Cloud restart clears research sessions.

## Cloud setup

1. In Streamlit Community Cloud, create an app from the GitHub repository and select `cloud_app.py` as its main file.
2. In **Advanced settings**, choose Python 3.12 and add root-level TOML secrets. Root-level secrets are exposed as environment variables to the app. At minimum, set a long random `EQUITYLENS_DEMO_PASSWORD`. Keep it out of the repository.
3. If you want optional Groq answers, add your own `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE_URL`, and `LLM_API_MODE`. For Groq's Chat Completions-compatible endpoint, use `https://api.groq.com/openai/v1` and `chat_completions`. Do not paste credentials into source files, issues, screenshots, or chat.
4. Deploy, enter the invitation password, and test company lookup, research, retrieval, and one cited answer. Review Cloud logs and the app's resource usage. Avoid broad sharing until the live sources and model rate limits behave reliably.

Example secret names (replace the placeholders in the **Cloud Secrets** editor, never in Git):

```toml
EQUITYLENS_DEMO_PASSWORD = "a-long-random-invitation-password"
LLM_API_KEY = "your-provider-key"
LLM_MODEL = "your-provider-model-id"
LLM_API_BASE_URL = "https://api.groq.com/openai/v1"
LLM_API_MODE = "chat_completions"
```

`LLM_*` entries are optional. `SEC_USER_AGENT` is also optional and should contain your own identifiable contact value if you enable SEC requests. The invitation password is a small-demo gate, not per-user authentication; Community Cloud app privacy settings may provide another layer of access control. In-process sessions are limited to one app process and disappear on restart. Public Yahoo and RSS integrations remain best-effort sources. The hosted app may need a smaller research workload if it reaches Community Cloud's changing memory or CPU limits.

Official deployment details: [file organization](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization), [dependencies](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies), [secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), and [deploy steps](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).
