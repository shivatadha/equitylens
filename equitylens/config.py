from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = ""
    data_dir: Path = ROOT / "data"
    sec_user_agent: str = ""
    max_upload_bytes: int = 25 * 1024 * 1024
    extraction_timeout_seconds: int = 60
    tavily_api_key: str = ""
    eodhd_api_key: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = Field(default="", max_length=150, pattern=r"^(?:[A-Za-z0-9][A-Za-z0-9_./:\-]{0,149})?$")
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_mode: Literal["responses", "chat_completions"] = "responses"
    llm_max_output_tokens: int = Field(default=4096, ge=256, le=16384)
    llm_timeout_seconds: float = Field(default=90, ge=5, le=90)
    llm_reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high", "xhigh"] = ""
    research_ttl_seconds: int = 1800
    rag_embedding_model: str = "BAAI/bge-small-en-v1.5"
    rag_model_cache: Path = ROOT / ".cache" / "models"
    rag_download_model_if_missing: bool = False
    rag_read_max_sources: int = 4

    @field_validator("llm_api_base_url")
    @classmethod
    def remote_model_endpoint(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.lower() in {"localhost", "localhost.localdomain"}
            or parsed.hostname.lower().endswith((".localhost", ".local"))
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path.split("/")
        ):
            raise ValueError("Use a public HTTPS API base URL without credentials, query or fragment.")
        _ = parsed.port  # Validate the port syntax.
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("The answer-model API must not use a private or local IP address.")
        return value.rstrip("/")

    def generation_configured(self, model=""):
        return bool(self.llm_api_key.get_secret_value().strip() and (model or self.llm_model))

    @property
    def storage(self):
        return self.data_dir if self.data_dir.is_absolute() else ROOT / self.data_dir
