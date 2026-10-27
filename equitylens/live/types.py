from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .llm import MODEL_PATTERN


class CompanyMatch(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.^=\-]{0,24}$")
    name: str = Field(min_length=1, max_length=200)
    exchange: str
    market: Literal["US", "IN"]
    industry: str = ""
    sector: str = ""
    provider: str = "Yahoo Finance public search"
    retrieved_at: datetime


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_token: str = Field(max_length=6000)
    news_days: int = Field(default=7, ge=1, le=90)
    depth: Literal["quick", "detailed"] = "quick"
    tavily_api_key: SecretStr | None = None
    eodhd_api_key: SecretStr | None = None
    llm_model: str = Field(default="", max_length=150, pattern=MODEL_PATTERN)


class Evidence(BaseModel):
    id: str = ""
    title: str
    url: str
    publisher: str
    published_at: datetime | None = None
    retrieved_at: datetime
    category: str
    categories: list[str] = Field(default_factory=list)
    content: str = ""
    evidence_level: Literal[
        "headline", "search_excerpt", "page_excerpt", "pdf_excerpt", "structured_fact", "provider_profile",
        "official_feed_summary"
    ] = "headline"
    locator: str = ""


class FollowUpInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=1000)
    llm_model: str = Field(default="", max_length=150, pattern=MODEL_PATTERN)


class RetrievalInput(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    top_k: int = Field(default=6, ge=1, le=10)
