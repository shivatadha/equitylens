from sqlalchemy import select

from .models import Company, Listing, Source


def seed(session):
    sources = [
        (
            "sec-edgar",
            "SEC EDGAR",
            "regulator",
            "Public filing access subject to SEC fair-access policy. Configure a real SEC_USER_AGENT. No market prices.",
        ),
        (
            "company-ir",
            "Company investor relations",
            "issuer",
            "Selected public issuer documents for local research. Publication rights must be reviewed before redistribution.",
        ),
        (
            "user-upload",
            "User-provided document",
            "upload",
            "User-supplied evidence. Identity, publication dates and contents are not independently verified.",
        ),
    ]
    for sid, name, kind, policy in sources:
        if not session.get(Source, sid):
            session.add(Source(id=sid, name=name, kind=kind, policy=policy))
    companies = [
        ("Microsoft Corporation", "US", "USD", "0000789019", [("NASDAQ", "MSFT", "common_stock", "USD")]),
        (
            "Infosys Limited",
            "IN",
            "INR",
            "0001067491",
            [
                ("NSE", "INFY", "equity", "INR"),
                ("BSE", "500209", "equity", "INR"),
                ("NYSE", "INFY", "depositary_receipt", "USD"),
            ],
        ),
    ]
    for name, country, currency, cik, listings in companies:
        company = session.scalar(select(Company).where(Company.cik == cik))
        if not company:
            company = Company(name=name, country=country, reporting_currency=currency, cik=cik)
            session.add(company)
            session.flush()
        for exchange, ticker, security_type, trading_currency in listings:
            if not session.scalar(
                select(Listing).where(Listing.exchange == exchange, Listing.ticker == ticker)
            ):
                session.add(
                    Listing(
                        company_id=company.id,
                        exchange=exchange,
                        ticker=ticker,
                        security_type=security_type,
                        currency=trading_currency,
                    )
                )
    session.commit()
