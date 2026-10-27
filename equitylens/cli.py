import argparse
import json
import os

from sqlalchemy import select

from .config import Settings
from .database import make_engine, session_factory
from .models import Company
from .seed import seed


def main():
    parser = argparse.ArgumentParser(description="EquityLens local development tools")
    parser.add_argument("command", choices=["seed", "import-samples", "serve", "archive-serve"])
    args = parser.parse_args()
    if args.command in {"serve", "archive-serve"}:
        import uvicorn

        module = "equitylens.live_api:create_app" if args.command == "serve" else "equitylens.api:create_app"
        host = os.getenv("EQUITYLENS_BIND_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("EQUITYLENS_BIND_HOST must be 127.0.0.1 or 0.0.0.0.")
        uvicorn.run(module, factory=True, host=host, port=8001, access_log=False)
        return
    settings = Settings()
    factory = session_factory(make_engine(settings.database_url))
    if args.command == "seed":
        with factory() as session:
            seed(session)
        print("Registered Microsoft and Infosys, including their distinct exchange listings.")
    else:
        from urllib.parse import urlsplit

        from .adapters import fetch_public
        from .ingestion import ingest, media_type

        for key in ["microsoft-annual-2025", "infosys-annual-2025"]:
            try:
                item, content, _ = fetch_public(key, settings)
                with factory() as session:
                    company = session.scalar(select(Company).where(Company.cik == item.cik))
                    if company is None:
                        raise ValueError("Run seed first.")
                    company_id = company.id
                result = ingest(
                    factory,
                    settings,
                    company_id=company_id,
                    source_id="company-ir",
                    source_key=item.url,
                    title=item.title,
                    content=content,
                    kind=media_type(content, urlsplit(item.url).path),
                    source_url=item.url,
                    period_end=item.period_end,
                )
                print(json.dumps({"catalog_key": key, **result}), flush=True)
            except Exception as exc:
                print(json.dumps({"catalog_key": key, "error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
