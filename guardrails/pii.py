"""PII rail — Microsoft Presidio scans agent outputs before they hit logs.

Synthetic data contains fake PII (Faker-generated names, addresses, emails).
The rail demonstrates the data-protection pattern banks need: detect, log,
redact. We use the analyzer to scan and the anonymizer to redact.

Performance note: Presidio's NLP model is heavy. We lazy-load it on first
use and reuse the singleton across the process.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from guardrails.audit import write_security_event

_ANALYZER = None
_ANONYMIZER = None

# PII entities Presidio recognises out of the box that we care about for
# banking data. Keep this narrow — false positives noise up the audit log.
_ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "LOCATION"]


def _analyzer():
    global _ANALYZER
    if _ANALYZER is None:
        from presidio_analyzer import AnalyzerEngine

        _ANALYZER = AnalyzerEngine()
    return _ANALYZER


def _anonymizer():
    global _ANONYMIZER
    if _ANONYMIZER is None:
        from presidio_anonymizer import AnonymizerEngine

        _ANONYMIZER = AnonymizerEngine()
    return _ANONYMIZER


def scan_for_pii(text: str) -> list[dict[str, Any]]:
    """Return Presidio analyzer results as plain dicts (suitable for JSONB)."""
    if not text or not text.strip():
        return []
    results = _analyzer().analyze(text=text, entities=_ENTITIES, language="en")
    return [
        {
            "entity_type": r.entity_type,
            "start": r.start,
            "end": r.end,
            "score": r.score,
        }
        for r in results
    ]


def redact_pii(
    text: str,
    *,
    actor: str = "guardrails",
    investigation_id: uuid.UUID | None = None,
) -> str:
    """Return `text` with PII entities replaced by `<TYPE>` placeholders.

    Logs a `security_events` row noting which entity types were redacted.
    Always returns a string, even on Presidio errors (fail-open, since
    redaction in the audit log is a defence-in-depth measure, not the
    primary data-protection mechanism)."""
    if not text or not text.strip():
        return text
    try:
        results = _analyzer().analyze(text=text, entities=_ENTITIES, language="en")
        if not results:
            return text
        anonymized = _anonymizer().anonymize(text=text, analyzer_results=results)
        entity_types = sorted({r.entity_type for r in results})
        write_security_event(
            rail="pii",
            actor=actor,
            severity="info",
            detail=f"Redacted {len(results)} PII instances of types {entity_types}",
            investigation_id=investigation_id,
            payload={"types": entity_types, "count": len(results)},
        )
        return anonymized.text
    except Exception as e:
        write_security_event(
            rail="pii",
            actor=actor,
            severity="error",
            detail=f"PII redaction failed: {e}",
            investigation_id=investigation_id,
        )
        return text  # fail-open


def redact_payload(
    payload: dict[str, Any],
    *,
    actor: str = "guardrails",
    investigation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Walk a JSON-serialisable payload and redact PII from string values."""
    serialised = json.dumps(payload, default=str)
    redacted = redact_pii(serialised, actor=actor, investigation_id=investigation_id)
    try:
        return json.loads(redacted)
    except json.JSONDecodeError:
        # Redaction broke structure (rare). Return original to keep audit log valid.
        return payload
