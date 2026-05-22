"""Single seam between local Ollama (dev) and Bedrock (demo).

No agent ever imports `ChatOllama` or `ChatBedrock` directly — only `get_llm(role)`.
See plan: "The LLM Abstraction Layer" and ADR-006.
"""
from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel

from core.settings import get_settings

# ----------------------------------------------------------------------------
# Role registry — keep in sync with plan section "Per-agent local model
# assignment". Adding a new role? Add it in BOTH maps.
# ----------------------------------------------------------------------------

Role = Literal[
    "triage",
    "account_historian",
    "network_mapper",
    "pattern_hunter",
    "case_writer",
    "judge",  # LLM-as-judge — always Bedrock, see plan 3.3
]

OLLAMA_MODELS: dict[str, str] = {
    # Tags follow Ollama library conventions: instruct/chat is the default
    # variant for these models, no `-instruct` suffix needed.
    "triage": "qwen3:4b",
    "account_historian": "qwen3:8b",
    "network_mapper": "qwen3:8b",
    "pattern_hunter": "qwen3:8b",
    "case_writer": "llama3.1:8b",
    # 'judge' deliberately omitted — always Bedrock, see get_llm().
}

# Roles that should respond with strict JSON (Ollama `format="json"` mode).
JSON_ROLES: set[str] = {
    "triage",
    "account_historian",
    "network_mapper",
    "pattern_hunter",
    "case_writer",
    "judge",
}


def _make_ollama(role: Role) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    settings = get_settings()
    if role not in OLLAMA_MODELS:
        raise ValueError(f"No local Ollama model registered for role={role!r}")
    return ChatOllama(
        model=OLLAMA_MODELS[role],
        base_url=settings.ollama_host,
        keep_alive=settings.ollama_keep_alive,
        format="json" if role in JSON_ROLES else None,
        temperature=0.1,
    )


def _make_bedrock(role: Role) -> BaseChatModel:
    from langchain_aws import ChatBedrock

    settings = get_settings()
    model_id = (
        settings.bedrock_model_judge if role == "judge" else settings.bedrock_model_default
    )
    return ChatBedrock(
        model_id=model_id,
        region_name=settings.aws_region,
        model_kwargs={"temperature": 0.1, "max_tokens": 4096},
    )


def _make_anthropic(role: Role) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    model_attr = f"anthropic_model_{role}"
    model_id = getattr(settings, model_attr, settings.anthropic_model_triage)
    return ChatAnthropic(
        model=model_id,
        api_key=settings.anthropic_api_key,
        temperature=0.1,
        max_tokens=4096,
    )


def _make_vertex(role: Role) -> BaseChatModel:
    from langchain_google_vertexai import ChatVertexAI

    settings = get_settings()
    model_name = (
        settings.vertex_model_judge if role == "judge" else settings.vertex_model_default
    )
    return ChatVertexAI(
        model_name=model_name,
        project=settings.gcp_project_id or None,
        location=settings.gcp_region,
        temperature=0.1,
        max_output_tokens=4096,
    )


def get_llm(role: Role) -> BaseChatModel:
    """Return a chat model for `role`.

    Agents follow LLM_BACKEND (ollama | bedrock | vertex).
    Judge follows JUDGE_BACKEND (bedrock | vertex) — never Ollama, since an
    8B model grading another 8B model is not credible (plan 3.3). Keeping
    judge on a separate setting lets you run agents on Vertex (UK demo) but
    grade with Bedrock for cross-vendor neutrality, or vice-versa.
    """
    settings = get_settings()
    if role == "judge":
        if settings.judge_backend == "vertex":
            return _make_vertex(role)
        if settings.judge_backend == "anthropic":
            return _make_anthropic(role)
        return _make_bedrock(role)
    if settings.llm_backend == "bedrock":
        return _make_bedrock(role)
    if settings.llm_backend == "vertex":
        return _make_vertex(role)
    if settings.llm_backend == "anthropic":
        return _make_anthropic(role)
    return _make_ollama(role)
