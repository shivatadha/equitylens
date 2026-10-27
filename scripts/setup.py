"""Generate local configuration; never print the database password."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env = root / ".env"
if not env.exists():
    password = secrets.token_urlsafe(32)
    env.write_text(
        f"DATABASE_URL=postgresql+psycopg://equitylens:{password}@127.0.0.1:55432/equitylens\nPOSTGRES_PASSWORD={password}\nDATA_DIR=data\nSEC_USER_AGENT=\nAPI_BASE_URL=http://127.0.0.1:8001\n"
        "LLM_API_KEY=\nLLM_MODEL=\nLLM_API_BASE_URL=https://api.openai.com/v1\nLLM_API_MODE=responses\n",
        encoding="utf-8",
    )
    print("Created local configuration with a generated database password.")
else:
    print("Existing local configuration retained.")
