"""Synthetic dataset generator — see plan section 0.2.

Run: `python -m data.generate [--seed N]`. Idempotent: truncates target tables first.

Volumes (matches plan):
    10,000 accounts
   500,000 transactions
        50 fraud scenarios across 5 typologies
       200 watchlist entries
        30 alerts (20 genuine, 10 false positive)

Ground truth labels are written to `eval/ground_truth.json` — never to the DB,
so the agents cannot accidentally read them.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from faker import Faker
from sqlalchemy import text

from core.db import session_scope
from core.models import (
    Account,
    AccountNetworkEdge,
    FraudAlert,
    Transaction,
    WatchlistEntity,
)
from data.fraud_scenarios import SCENARIO_GENERATORS, Scenario

log = logging.getLogger("data.generate")

# Volumes
N_ACCOUNTS = 10_000
N_BASELINE_TRANSACTIONS = 500_000
N_WATCHLIST = 200
SCENARIOS_PER_TYPOLOGY = 10
N_FALSE_POSITIVE_ALERTS = 10
HISTORY_DAYS = 180

# Bulk insert chunk size (avoids OOM and gives steady progress).
CHUNK = 10_000


# ----------------------------------------------------------------------------
# Lightweight in-memory mirrors. We materialise the world in RAM, then bulk-write.
# ----------------------------------------------------------------------------


@dataclass
class _Account:
    id: str
    account_type: str
    open_date: datetime
    kyc_tier: int
    country: str
    holder_name: str
    holder_address: str
    device_fingerprint: str | None
    primary_ip: str | None
    beneficial_owner_id: str | None = None
    status: str = "active"


@dataclass
class _WatchlistEntity:
    id: str
    name: str
    list_type: str
    country: str | None
    risk_score: int


@dataclass
class _Edge:
    source_account_id: str
    target_account_id: str
    relationship_type: str
    source_type: str
    weight: float
    metadata: dict[str, Any]


# ----------------------------------------------------------------------------
# World — the mutable scratchpad scenario generators write into.
# ----------------------------------------------------------------------------


class World:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.fake = Faker("en_GB")
        Faker.seed(seed)
        self.now = datetime.now(timezone.utc)
        self.accounts: dict[str, _Account] = {}
        self.watchlist: list[_WatchlistEntity] = []
        self.transactions: list[dict[str, Any]] = []
        self.edges: list[_Edge] = []
        self._tx_id_counter = 1

    # --- accounts ------------------------------------------------------------

    def add_account(self, account: _Account) -> None:
        self.accounts[account.id] = account

    def update_account_attrs(self, account_id: str, **attrs: Any) -> None:
        acc = self.accounts[account_id]
        for k, v in attrs.items():
            setattr(acc, k, v)

    def pick_personal_account(
        self,
        opened_within_days: int | None = None,
        min_age_days: int | None = None,
    ) -> _Account:
        candidates = [
            a for a in self.accounts.values()
            if a.account_type == "personal"
            and (opened_within_days is None or (self.now - a.open_date).days <= opened_within_days)
            and (min_age_days is None or (self.now - a.open_date).days >= min_age_days)
        ]
        return self.rng.choice(candidates)

    def pick_business_account(self) -> _Account:
        return self.rng.choice([a for a in self.accounts.values() if a.account_type == "business"])

    def pick_unrelated_accounts(self, exclude_id: str | None, n: int) -> list[_Account]:
        pool = [a for a in self.accounts.values() if a.id != exclude_id]
        return self.rng.sample(pool, n)

    # --- watchlist -----------------------------------------------------------

    def pick_random_watchlist(self, list_type: str) -> _WatchlistEntity:
        return self.rng.choice([w for w in self.watchlist if w.list_type == list_type])

    # --- transactions --------------------------------------------------------

    def add_transaction(
        self,
        *,
        account_id: str,
        counterparty_account_id: str | None,
        amount: Decimal,
        direction: str,
        channel: str,
        merchant: str | None,
        merchant_category: str | None,
        description: str | None,
        timestamp: datetime,
    ) -> int:
        tx_id = self._tx_id_counter
        self._tx_id_counter += 1
        self.transactions.append({
            "id": tx_id,
            "account_id": account_id,
            "counterparty_account_id": counterparty_account_id,
            "amount": amount,
            "currency": "GBP",
            "direction": direction,
            "channel": channel,
            "merchant": merchant,
            "merchant_category": merchant_category,
            "description": description,
            "timestamp": timestamp,
            "status": "settled",
        })
        return tx_id

    # --- edges ---------------------------------------------------------------

    def add_edge(
        self,
        *,
        source: str,
        target: str,
        relationship_type: str,
        source_type: str,
        weight: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.edges.append(_Edge(
            source_account_id=source,
            target_account_id=target,
            relationship_type=relationship_type,
            source_type=source_type,
            weight=weight,
            metadata=metadata or {},
        ))


# ----------------------------------------------------------------------------
# Population steps
# ----------------------------------------------------------------------------


def populate_accounts(world: World) -> None:
    log.info("Generating %d accounts...", N_ACCOUNTS)
    for i in range(N_ACCOUNTS):
        is_business = world.rng.random() < 0.15
        opened = world.now - timedelta(days=world.rng.randint(30, 365 * 8))
        country = world.rng.choices(["GB", "IE", "FR", "DE", "ES", "NL"], weights=[80, 5, 4, 4, 4, 3])[0]
        world.add_account(_Account(
            id=f"AC{i:08d}",
            account_type="business" if is_business else "personal",
            open_date=opened,
            kyc_tier=world.rng.choices([1, 2, 3], weights=[20, 60, 20])[0],
            country=country,
            holder_name=world.fake.company() if is_business else world.fake.name(),
            holder_address=world.fake.address().replace("\n", ", "),
            device_fingerprint=world.fake.sha256()[:32] if not is_business else None,
            primary_ip=world.fake.ipv4_public() if not is_business else None,
        ))


def populate_watchlist(world: World) -> None:
    log.info("Generating %d watchlist entries...", N_WATCHLIST)
    for i in range(N_WATCHLIST):
        list_type = world.rng.choices(
            ["PEP", "SANCTIONS", "ADVERSE_MEDIA"], weights=[60, 25, 15]
        )[0]
        world.watchlist.append(_WatchlistEntity(
            id=f"WL{i:05d}",
            name=world.fake.name(),
            list_type=list_type,
            country=world.fake.country_code(),
            risk_score=world.rng.randint(40, 95),
        ))


def populate_baseline_transactions(world: World) -> None:
    log.info("Generating %d baseline transactions...", N_BASELINE_TRANSACTIONS)
    accounts = list(world.accounts.values())
    merchants_by_cat = {
        "groceries": ["Tesco", "Sainsbury's", "Aldi", "Waitrose", "Lidl"],
        "transport": ["TfL", "Uber", "Trainline", "Shell", "BP"],
        "dining": ["Pret", "Greggs", "Nando's", "Wagamama", "Pizza Express"],
        "retail": ["Amazon", "John Lewis", "Argos", "Boots"],
        "utilities": ["British Gas", "EDF Energy", "Thames Water", "BT"],
        "salary": ["Employer Inc", "ACME Ltd", "BigCo PLC"],
        "rent": ["Landlord Holdings"],
    }
    categories = list(merchants_by_cat.keys())

    for i in range(N_BASELINE_TRANSACTIONS):
        if i % 50_000 == 0 and i:
            log.info("  ... %d / %d baseline transactions", i, N_BASELINE_TRANSACTIONS)
        acc = world.rng.choice(accounts)
        days_ago = world.rng.randint(0, HISTORY_DAYS)
        ts = world.now - timedelta(days=days_ago, seconds=world.rng.randint(0, 86_399))
        cat = world.rng.choices(categories, weights=[25, 20, 18, 15, 8, 8, 6])[0]
        merchant = world.rng.choice(merchants_by_cat[cat])
        if cat == "salary":
            amount = Decimal(world.rng.randint(180_000, 750_000)) / Decimal(100)
            direction = "credit"
            channel = "transfer"
        elif cat == "rent":
            amount = Decimal(world.rng.randint(60_000, 300_000)) / Decimal(100)
            direction = "debit"
            channel = "transfer"
        else:
            # Lognormal-ish skewed amount for everyday spend.
            amount = Decimal(int(world.rng.lognormvariate(3.0, 0.9) * 100)) / Decimal(100)
            direction = "debit"
            channel = world.rng.choices(["card", "transfer", "atm"], weights=[80, 15, 5])[0]
        world.add_transaction(
            account_id=acc.id,
            counterparty_account_id=None,
            amount=amount,
            direction=direction,
            channel=channel,
            merchant=merchant,
            merchant_category=cat,
            description=None,
            timestamp=ts,
        )


def populate_declared_edges(world: World) -> None:
    """A small sprinkling of declared KYC links between business and personal accounts."""
    log.info("Generating declared (KYC) edges...")
    business_accounts = [a for a in world.accounts.values() if a.account_type == "business"]
    personal_accounts = [a for a in world.accounts.values() if a.account_type == "personal"]
    for biz in world.rng.sample(business_accounts, k=min(len(business_accounts), 200)):
        owner = world.rng.choice(personal_accounts)
        world.add_edge(
            source=biz.id, target=owner.id,
            relationship_type="beneficial_owner",
            source_type="declared", weight=1.0,
            metadata={"declared_at_onboarding": True},
        )


def inject_scenarios(world: World) -> list[Scenario]:
    log.info("Injecting %d scenarios...", SCENARIOS_PER_TYPOLOGY * len(SCENARIO_GENERATORS))
    scenarios: list[Scenario] = []
    for typology, generator in SCENARIO_GENERATORS.items():
        for i in range(SCENARIOS_PER_TYPOLOGY):
            scenarios.append(generator(world, scenario_idx=i + 1))
    return scenarios


def build_alerts(world: World, scenarios: list[Scenario]) -> tuple[list[dict], list[dict]]:
    """Return (alert_rows, ground_truth_rows). 20 genuine + 10 false positive."""
    log.info("Building 30 alerts (20 genuine, 10 false positive)...")
    rng = world.rng
    genuine_scenarios = rng.sample(scenarios, 20)
    alert_rows: list[dict] = []
    ground_truth: list[dict] = []
    alert_idx = 0
    for sc in genuine_scenarios:
        alert_idx += 1
        aid = f"ALERT{alert_idx:04d}"
        alert_rows.append({
            "id": aid,
            "account_id": sc.target_account_id,
            "alert_type": sc.typology,
            "initial_score": Decimal(str(round(rng.uniform(0.55, 0.92), 3))),
            "raised_at": world.now - timedelta(hours=rng.randint(1, 24)),
            "status": "open",
            "metadata_json": {"source": "rule_engine", "scenario_id": sc.scenario_id},
        })
        ground_truth.append({
            "alert_id": aid,
            "is_genuine": True,
            "scenario_id": sc.scenario_id,
            "expected_typology": sc.typology,
            "expected_verdict": "SAR_FILE" if sc.typology in {"STRUCTURING", "LAYERING", "MULE_NETWORK"} else "REVIEW",
            "expected_network_accounts": sc.accounts_involved,
            "expected_evidence": sc.expected_evidence,
        })

    # 10 false positives — point at a clean account, type chosen at random
    clean_accounts = [
        a for a in world.accounts.values()
        if not any(a.id in sc.accounts_involved for sc in scenarios)
    ]
    for clean in rng.sample(clean_accounts, N_FALSE_POSITIVE_ALERTS):
        alert_idx += 1
        aid = f"ALERT{alert_idx:04d}"
        alert_type = rng.choice(list(SCENARIO_GENERATORS.keys()))
        alert_rows.append({
            "id": aid,
            "account_id": clean.id,
            "alert_type": alert_type,
            "initial_score": Decimal(str(round(rng.uniform(0.40, 0.65), 3))),
            "raised_at": world.now - timedelta(hours=rng.randint(1, 24)),
            "status": "open",
            "metadata_json": {"source": "rule_engine", "scenario_id": None},
        })
        ground_truth.append({
            "alert_id": aid,
            "is_genuine": False,
            "scenario_id": None,
            "expected_typology": "NONE",
            "expected_verdict": "AUTO_CLOSE",
            "expected_network_accounts": [],
            "expected_evidence": [],
        })

    return alert_rows, ground_truth


# ----------------------------------------------------------------------------
# Persistence — bulk inserts in chunks
# ----------------------------------------------------------------------------


def _truncate_all(session) -> None:
    log.info("Truncating tables for idempotent re-run...")
    # Order matters: children before parents. CASCADE keeps it safe.
    session.execute(text("""
        TRUNCATE TABLE
            evaluation_results, evaluation_runs,
            tool_call_logs, agent_traces, security_events,
            case_files, evidence_items, agent_decisions,
            investigation_events, investigations, fraud_alerts,
            account_network_edges, fraud_pattern_embeddings,
            transactions, watchlist_entities, accounts
        RESTART IDENTITY CASCADE
    """))


def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def write_to_db(world: World, alerts: list[dict]) -> None:
    log.info("Writing to Postgres...")
    with session_scope() as session:
        _truncate_all(session)

        log.info("  accounts: %d", len(world.accounts))
        for chunk in _chunked(list(world.accounts.values()), CHUNK):
            session.bulk_save_objects([
                Account(
                    id=a.id, account_type=a.account_type, open_date=a.open_date,
                    kyc_tier=a.kyc_tier, country=a.country, status=a.status,
                    holder_name=a.holder_name, holder_address=a.holder_address,
                    device_fingerprint=a.device_fingerprint, primary_ip=a.primary_ip,
                    beneficial_owner_id=a.beneficial_owner_id,
                ) for a in chunk
            ])
            session.flush()

        log.info("  watchlist: %d", len(world.watchlist))
        session.bulk_save_objects([
            WatchlistEntity(
                id=w.id, name=w.name, list_type=w.list_type,
                country=w.country, risk_score=w.risk_score, metadata_json={},
            ) for w in world.watchlist
        ])
        session.flush()

        log.info("  transactions: %d (chunked)", len(world.transactions))
        for chunk in _chunked(world.transactions, CHUNK):
            session.bulk_insert_mappings(Transaction, chunk)
            session.flush()

        log.info("  edges: %d", len(world.edges))
        for chunk in _chunked(world.edges, CHUNK):
            session.bulk_save_objects([
                AccountNetworkEdge(
                    source_account_id=e.source_account_id,
                    target_account_id=e.target_account_id,
                    relationship_type=e.relationship_type,
                    source_type=e.source_type,
                    weight=e.weight,
                    metadata_json=e.metadata,
                ) for e in chunk
            ])
            session.flush()

        log.info("  alerts: %d", len(alerts))
        session.bulk_save_objects([FraudAlert(**a) for a in alerts])

    log.info("DB write complete.")


def write_ground_truth(ground_truth: list[dict], scenarios: list[Scenario]) -> None:
    out_dir = Path("eval")
    out_dir.mkdir(exist_ok=True)
    target = out_dir / "ground_truth.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "alerts": ground_truth,
        "scenarios": [
            {
                "scenario_id": sc.scenario_id,
                "typology": sc.typology,
                "target_account_id": sc.target_account_id,
                "accounts_involved": sc.accounts_involved,
                "pattern_descriptions": sc.pattern_descriptions,
                "expected_evidence": sc.expected_evidence,
                "transaction_ids": sc.transaction_ids,
            } for sc in scenarios
        ],
    }
    target.write_text(json.dumps(payload, indent=2, default=str))
    log.info("Ground truth written to %s", target)


# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    world = World(seed=args.seed)

    populate_accounts(world)
    populate_watchlist(world)
    populate_baseline_transactions(world)
    populate_declared_edges(world)
    scenarios = inject_scenarios(world)

    alerts, ground_truth = build_alerts(world, scenarios)

    write_to_db(world, alerts)
    write_ground_truth(ground_truth, scenarios)

    log.info("Done. %d accounts, %d transactions, %d scenarios, %d alerts.",
             len(world.accounts), len(world.transactions), len(scenarios), len(alerts))


if __name__ == "__main__":
    main()
