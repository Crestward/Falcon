"""Scope rail — Network Mapper cannot query accounts outside the
investigation's discovered network.

The agent passes `accounts_in_scope` (recomputed by the supervisor at every
checkpoint). The decorator inspects the call's seed account against that set
and refuses + audits attempts to step outside.

Regulatory rationale: a misconfigured Network Mapper that pulls arbitrary
accounts would breach minimum-necessary data principles (GDPR Art. 5(1)(c))
and the FCA's customer-data handling expectations. Hard-stop in code.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from guardrails.audit import write_security_event

P = ParamSpec("P")
R = TypeVar("R")


class ScopeViolation(Exception):
    """Raised when a tool call targets an out-of-scope account."""


def enforce_scope(
    seed_arg: str,
    actor: str,
    *,
    scope: Iterable[str],
    investigation_id: uuid.UUID | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator factory. Wraps a tool function so any value passed as
    `seed_arg` must be in `scope`. Logs to security_events on violation."""
    scope_set = set(scope)

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            seed = kwargs.get(seed_arg)
            if seed is None and args:
                # Best-effort positional lookup — most tool calls use kwargs.
                seed = args[0]
            if seed is not None and seed not in scope_set:
                write_security_event(
                    rail="scope",
                    actor=actor,
                    detail=f"Tool call attempted on out-of-scope account {seed!r}",
                    investigation_id=investigation_id,
                    payload={"seed": str(seed), "scope_size": len(scope_set)},
                )
                raise ScopeViolation(
                    f"{actor}: account {seed!r} is not in investigation scope "
                    f"({len(scope_set)} accounts allowed)"
                )
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def is_in_scope(account_id: str, scope: Iterable[str]) -> bool:
    """Cheap check for use outside the decorator path."""
    return account_id in set(scope)


def assert_in_scope(
    account_id: str,
    actor: str,
    *,
    scope: Iterable[str],
    investigation_id: uuid.UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Inline check for tool wrappers that aren't easily decorated."""
    scope_set = set(scope)
    if account_id not in scope_set:
        write_security_event(
            rail="scope",
            actor=actor,
            detail=f"Out-of-scope access blocked: {account_id!r}",
            investigation_id=investigation_id,
            payload={"account_id": account_id, "scope_size": len(scope_set), **(extra or {})},
        )
        raise ScopeViolation(
            f"{actor}: account {account_id!r} not in scope ({len(scope_set)} allowed)"
        )
