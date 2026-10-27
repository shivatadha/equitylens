from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid4())


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2))
    reporting_currency: Mapped[str] = mapped_column(String(3))
    cik: Mapped[str | None] = mapped_column(String(10), unique=True)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("exchange", "ticker", name="uq_listing_exchange_ticker"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(16))
    ticker: Mapped[str] = mapped_column(String(32))
    security_type: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    policy: Mapped[str] = mapped_column(Text)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("company_id", "source_id", "source_key", name="uq_document_identity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_key: Mapped[str] = mapped_column(String(1200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "sha256", name="uq_document_content"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    published_on: Mapped[date | None] = mapped_column(Date)
    publication_basis: Mapped[str] = mapped_column(String(40))
    period_end: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(1200))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    parser_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    warning: Mapped[str | None] = mapped_column(Text)


class EvidenceChunk(Base):
    __tablename__ = "evidence_chunks"
    __table_args__ = (UniqueConstraint("version_id", "ordinal", name="uq_chunk_ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    locator: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    status: Mapped[str] = mapped_column(String(40))
    version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (UniqueConstraint("identity_hash", name="uq_fact_identity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"))
    identity_hash: Mapped[str] = mapped_column(String(64))
    taxonomy: Mapped[str] = mapped_column(String(40))
    concept: Mapped[str] = mapped_column(String(180))
    label: Mapped[str] = mapped_column(String(300))
    value: Mapped[Decimal] = mapped_column(Numeric(38, 8))
    unit: Mapped[str] = mapped_column(String(60))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    filed_on: Mapped[date] = mapped_column(Date)
    form: Mapped[str] = mapped_column(String(24))
    accession: Mapped[str] = mapped_column(String(40))
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_period: Mapped[str | None] = mapped_column(String(12))
    locator: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON)


Index("ix_facts_company_period", FinancialFact.company_id, FinancialFact.period_end)
