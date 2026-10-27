# Invite-only EquityLens demo

`compose.demo.yaml` prepares the live API and Streamlit UI for a single-host demonstration. The API remains on the container network; the UI listens on the host's loopback address for an HTTPS reverse proxy. A visitor must enter an invitation password before the UI calls the API. This is a portfolio staging setup, not account authentication or a multi-user production service.

## Host setup

1. Use a host with Docker Compose, enough memory for the local embedding model, outbound access to public research sources, and an HTTPS reverse proxy that forwards WebSocket connections to `127.0.0.1:8501`.
2. Place the project on the host. Keep `.env` private and outside version control. Configure optional model credentials only if model answers are wanted. Leave `SEC_USER_AGENT` blank unless you have a real contact value.
3. Set `EQUITYLENS_DEMO_PASSWORD` to a long random invitation password in the host's deployment environment. Compose refuses to start if it is missing. Do not put it in a committed file or a command in shell history. Set `EQUITYLENS_UI_PORT` if 8501 is occupied.
4. From the project directory, run `docker compose -f compose.demo.yaml up --build -d`. Point your HTTPS proxy's upstream at `127.0.0.1:8501`. Keep proxy request limits and access logs enabled for a public address.
5. Confirm the password prompt appears through HTTPS, an incorrect password is rejected, a correct password opens the app, and company lookup/research works. The API health check is available inside the container stack. Check `docker compose -f compose.demo.yaml ps` and `docker compose -f compose.demo.yaml logs --tail 100 api ui` after startup.

The password protects the interactive UI, not the application as a whole. Share it with invited reviewers only. Replace it after a demonstration. A public, unrestricted service would need per-user identity, server-side quotas, monitoring, durable session storage, and reviewed rights for displayed third-party data. Keep a single API replica because research sessions are currently in process memory. Restarting the API expires active sessions.

No domain, hosting account, or TLS certificate is included in this repository; this compose file does not publish a public URL by itself.
