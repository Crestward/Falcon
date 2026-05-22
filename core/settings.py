"""Centralised settings. Read once, injected everywhere."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM backend -----------------------------------------------------
    # Agent backend (judge backend is independent — see judge_backend below).
    llm_backend: Literal["ollama", "bedrock", "vertex", "anthropic"] = "ollama"
    # Judge backend. Defaults to bedrock for cross-vendor neutrality even
    # when agents run on vertex/ollama. See plan 3.3 and llm_factory.get_llm.
    judge_backend: Literal["bedrock", "vertex", "anthropic"] = "bedrock"

    ollama_host: str = "http://localhost:11434"
    # Ollama keep_alive: negative int = keep model in VRAM indefinitely.
    # If you ever want a duration string ("5m", "1h"), use the OLLAMA_KEEP_ALIVE_DURATION
    # env var path instead; the int form is what the JSON API expects.
    ollama_keep_alive: int = -1

    aws_region: str = "us-east-1"
    bedrock_model_default: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_model_judge: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_embedding_model: str = "amazon.titan-embed-text-v2:0"

    # --- Anthropic API (direct) ------------------------------------------
    # Cheapest & fastest dev backend. Prompt caching is enabled in
    # agents.llm_utils for this backend (90% cost cut on the static system block).
    anthropic_api_key: str = ""
    # Per-role model map. Set the same value everywhere for Phase 1
    # (Haiku 4.5). When you want to split, override individual roles.
    anthropic_model_triage: str = "claude-haiku-4-5-20251001"
    anthropic_model_account_historian: str = "claude-haiku-4-5-20251001"
    anthropic_model_network_mapper: str = "claude-haiku-4-5-20251001"
    anthropic_model_pattern_hunter: str = "claude-haiku-4-5-20251001"
    anthropic_model_case_writer: str = "claude-haiku-4-5-20251001"
    anthropic_model_judge: str = "claude-sonnet-4-6"

    # --- Vertex AI (GCP) -------------------------------------------------
    # Auth: Application Default Credentials. Locally:
    #   gcloud auth application-default login
    # In GKE/Cloud Run: workload identity. No keys live in .env.
    # Region: europe-west2 (London) for UK bank data residency (FCA/PRA, GDPR).
    gcp_project_id: str = ""
    gcp_region: str = "europe-west2"
    vertex_model_default: str = "gemini-2.5-flash"
    vertex_model_judge: str = "gemini-2.5-pro"

    # --- Database --------------------------------------------------------
    database_url: str = "postgresql+psycopg://falcon:falcon_dev_only@localhost:5432/falcon"

    # --- Observability ---------------------------------------------------
    otel_service_name: str = "falcon"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_exporter_otlp_insecure: bool = True

    langchain_tracing_v2: bool = False
    langchain_project: str = "falcon-dev"

    # --- MCP ports -------------------------------------------------------
    mcp_transaction_store_port: int = 8001
    mcp_network_graph_port: int = 8002
    mcp_watchlist_port: int = 8003
    mcp_case_management_port: int = 8004

    # --- API -------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Embedding dimension — `nomic-embed-text` and `titan-embed-text-v2` both default to 768.
    embedding_dim: int = Field(default=768, description="pgvector column width")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
