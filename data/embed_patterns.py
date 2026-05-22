"""Build the fraud-pattern embedding corpus — see plan section 0.2.1.

Reads `eval/ground_truth.json` (produced by `data.generate`), embeds every
`pattern_descriptions[*]` string with the configured embedding backend, and
inserts into `fraud_pattern_embeddings`.

Run after `python -m data.generate`:

    python -m data.embed_patterns

Backend follows LLM_BACKEND:
  - ollama  -> nomic-embed-text via OllamaEmbeddings
  - bedrock -> Titan via BedrockEmbeddings
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from langchain_core.embeddings import Embeddings
from sqlalchemy import text

from core.db import session_scope
from core.models import FraudPatternEmbedding
from core.settings import get_settings

log = logging.getLogger("data.embed_patterns")


def _make_embedder() -> Embeddings:
    settings = get_settings()
    if settings.llm_backend == "bedrock":
        from langchain_aws import BedrockEmbeddings
        return BedrockEmbeddings(
            model_id=settings.bedrock_embedding_model,
            region_name=settings.aws_region,
        )
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(
        model="nomic-embed-text",
        base_url=settings.ollama_host,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=Path("eval/ground_truth.json"),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.ground_truth.exists():
        raise SystemExit(
            f"{args.ground_truth} not found. Run `python -m data.generate` first."
        )
    payload = json.loads(args.ground_truth.read_text())
    scenarios = payload["scenarios"]

    embedder = _make_embedder()

    descriptions: list[tuple[str, str, str]] = []  # (typology, scenario_id, text)
    for sc in scenarios:
        for desc in sc["pattern_descriptions"]:
            descriptions.append((sc["typology"], sc["scenario_id"], desc))

    log.info("Embedding %d descriptions across %d scenarios...", len(descriptions), len(scenarios))
    vectors = embedder.embed_documents([d[2] for d in descriptions])

    if vectors and len(vectors[0]) != get_settings().embedding_dim:
        raise SystemExit(
            f"Embedding dimension mismatch: model returned {len(vectors[0])}, "
            f"settings.embedding_dim={get_settings().embedding_dim}. "
            "Update Settings.embedding_dim and rerun the migration."
        )

    with session_scope() as session:
        session.execute(text("TRUNCATE TABLE fraud_pattern_embeddings RESTART IDENTITY"))
        session.bulk_save_objects([
            FraudPatternEmbedding(
                typology=typology,
                description=desc,
                embedding=vec,
                source_scenario_id=scenario_id,
            )
            for (typology, scenario_id, desc), vec in zip(descriptions, vectors)
        ])
        # Rebuild ivfflat with `lists` tuned for our actual row count.
        # Rule of thumb: lists ≈ rows / 1000, min 1.
        lists = max(1, len(descriptions) // 50)
        session.execute(text("DROP INDEX IF EXISTS ix_fraud_pattern_embeddings_cos"))
        session.execute(text(
            f"CREATE INDEX ix_fraud_pattern_embeddings_cos "
            f"ON fraud_pattern_embeddings USING ivfflat (embedding vector_cosine_ops) "
            f"WITH (lists = {lists})"
        ))

    log.info("Inserted %d pattern embeddings.", len(descriptions))


if __name__ == "__main__":
    main()
