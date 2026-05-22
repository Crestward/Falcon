"""FALCON guardrails — 5 explicit rails, no framework dependency.

Each rail is a synchronous validator on structured data, applied at a
defined choke point. Violations are logged to `security_events`.

  scope.py         Network Mapper cannot query accounts outside investigation
  schema.py        Pydantic validation at every agent boundary
  justification.py Every tool call must carry a non-empty justification
  escalation.py    Case Writer cannot emit SAR_FILE without enough evidence
  pii.py           Presidio scrubs PII from agent outputs before logging

See ADR-005 for the "custom vs NeMo Guardrails" rationale.
"""
from guardrails.audit import write_security_event
from guardrails.escalation import enforce_escalation
from guardrails.justification import JustificationViolation, enforce_justification
from guardrails.pii import redact_payload, redact_pii, scan_for_pii
from guardrails.schema import SchemaViolation, enforce_schema
from guardrails.scope import ScopeViolation, assert_in_scope, enforce_scope, is_in_scope

__all__ = [
    "write_security_event",
    "enforce_escalation",
    "enforce_justification",
    "JustificationViolation",
    "redact_pii",
    "redact_payload",
    "scan_for_pii",
    "enforce_schema",
    "SchemaViolation",
    "enforce_scope",
    "assert_in_scope",
    "is_in_scope",
    "ScopeViolation",
]
