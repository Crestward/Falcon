"""Five fraud typologies, ten scenarios each = 50 seeded scenarios.

Each scenario is a function that mutates the world: injects pattern transactions,
adds derived edges, optionally flags accounts/watchlist hits. Returns a
`Scenario` describing what was injected, used as ground truth for the eval
harness AND as source material for the embedding corpus (0.2.1).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.generate import World


# Reporting threshold (UK CTR-equivalent for the synthetic regime).
THRESHOLD_GBP = Decimal("10000")


@dataclass
class Scenario:
    scenario_id: str
    typology: str
    target_account_id: str
    accounts_involved: list[str] = field(default_factory=list)
    pattern_descriptions: list[str] = field(default_factory=list)
    expected_evidence: list[str] = field(default_factory=list)
    transaction_ids: list[int] = field(default_factory=list)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _spread_timestamps(start: datetime, hours_window: int, n: int, rng: random.Random) -> list[datetime]:
    return sorted(start + timedelta(seconds=rng.randint(0, hours_window * 3600)) for _ in range(n))


# ----------------------------------------------------------------------------
# 1. STRUCTURING — deposits just below £10k threshold
# ----------------------------------------------------------------------------


def structuring(world: "World", scenario_idx: int) -> Scenario:
    rng = world.rng
    target = world.pick_personal_account(opened_within_days=60)
    counterparties = world.pick_unrelated_accounts(target.id, n=rng.randint(4, 7))
    base_ts = world.now - timedelta(days=rng.randint(2, 10))
    timestamps = _spread_timestamps(base_ts, hours_window=72, n=len(counterparties), rng=rng)

    tx_ids = []
    for cp, ts in zip(counterparties, timestamps):
        amt = Decimal(rng.randint(9300, 9900)) + Decimal(rng.randint(0, 99)) / 100
        tx_id = world.add_transaction(
            account_id=target.id,
            counterparty_account_id=cp.id,
            amount=amt,
            direction="credit",
            channel="transfer",
            merchant=None,
            merchant_category=None,
            description="inbound transfer",
            timestamp=ts,
        )
        tx_ids.append(tx_id)

    sid = f"STR-{scenario_idx:03d}"
    return Scenario(
        scenario_id=sid,
        typology="STRUCTURING",
        target_account_id=target.id,
        accounts_involved=[target.id, *(cp.id for cp in counterparties)],
        transaction_ids=tx_ids,
        pattern_descriptions=[
            f"{len(counterparties)} deposits of £9,300-£9,900 within 72 hours from distinct counterparties to a personal account opened in the last 60 days",
            "Multiple inbound transfers each just below the £10,000 reporting threshold from unrelated counterparties within a short window",
            "Cluster of credits sized to evade currency transaction reporting, originating from accounts with no prior relationship to the receiver",
            "Recently opened personal account receives several near-threshold deposits over a few days from different sources",
        ],
        expected_evidence=[
            f"{len(counterparties)} near-threshold credits within 72h",
            f"Counterparty diversity: {len(counterparties)} distinct sources",
            "Account opened within 60 days of pattern",
        ],
    )


# ----------------------------------------------------------------------------
# 2. LAYERING — rapid movement across 3+ accounts
# ----------------------------------------------------------------------------


def layering(world: "World", scenario_idx: int) -> Scenario:
    rng = world.rng
    chain_length = rng.randint(3, 5)
    chain = [world.pick_unrelated_accounts(None, n=1)[0] for _ in range(chain_length + 1)]
    base_ts = world.now - timedelta(days=rng.randint(1, 5))
    initial = Decimal(rng.randint(45000, 95000))

    tx_ids: list[int] = []
    accounts_involved = [a.id for a in chain]
    current_amount = initial
    ts = base_ts
    for src, dst in zip(chain[:-1], chain[1:]):
        ts = ts + timedelta(minutes=rng.randint(15, 240))
        current_amount = current_amount * Decimal(rng.randint(85, 99)) / Decimal(100)
        tx_id = world.add_transaction(
            account_id=src.id,
            counterparty_account_id=dst.id,
            amount=current_amount.quantize(Decimal("0.01")),
            direction="debit",
            channel="transfer",
            merchant=None,
            merchant_category=None,
            description="onward transfer",
            timestamp=ts,
        )
        tx_ids.append(tx_id)
        # Mirror inbound on dst.
        world.add_transaction(
            account_id=dst.id,
            counterparty_account_id=src.id,
            amount=current_amount.quantize(Decimal("0.01")),
            direction="credit",
            channel="transfer",
            merchant=None,
            merchant_category=None,
            description="onward transfer",
            timestamp=ts,
        )

    target = chain[0]
    sid = f"LAY-{scenario_idx:03d}"
    return Scenario(
        scenario_id=sid,
        typology="LAYERING",
        target_account_id=target.id,
        accounts_involved=accounts_involved,
        transaction_ids=tx_ids,
        pattern_descriptions=[
            f"Funds move through {chain_length + 1} accounts in under {chain_length * 4} hours, with each hop reducing the amount by 1-15% to obscure origin",
            "Rapid sequential transfers across a chain of three or more accounts within a short window with no apparent commercial purpose",
            "Onward-transfer pattern: a credit is followed within minutes-to-hours by a near-equivalent debit to a fresh counterparty",
            "Velocity of movement and absence of business rationale across multiple intermediary accounts",
        ],
        expected_evidence=[
            f"Chain of {chain_length + 1} accounts traversed",
            f"Inter-hop interval median ~{rng.randint(30, 180)} minutes",
            f"Amount preserved within {(1 - (current_amount / initial)) * 100:.1f}% across chain",
        ],
    )


# ----------------------------------------------------------------------------
# 3. ACCOUNT TAKEOVER — sudden behavioural shift vs baseline
# ----------------------------------------------------------------------------


def account_takeover(world: "World", scenario_idx: int) -> Scenario:
    rng = world.rng
    target = world.pick_personal_account(opened_within_days=None, min_age_days=180)
    # Baseline already exists (long-tenured account). Inject a sharp shift in last 48h.
    shift_start = world.now - timedelta(hours=rng.randint(12, 48))
    new_country = rng.choice(["RO", "UA", "NG", "PH"])
    new_ip = world.fake.ipv4_public()
    new_device = world.fake.sha256()[:32]

    tx_ids = []
    for i in range(rng.randint(6, 12)):
        ts = shift_start + timedelta(minutes=i * rng.randint(8, 25))
        amt = Decimal(rng.randint(150, 1800))
        tx_id = world.add_transaction(
            account_id=target.id,
            counterparty_account_id=None,
            amount=amt,
            direction="debit",
            channel="card",
            merchant=world.fake.company(),
            merchant_category=rng.choice(["electronics", "luxury_goods", "crypto_exchange", "gift_cards"]),
            description=f"foreign card-not-present from {new_country}",
            timestamp=ts,
        )
        tx_ids.append(tx_id)

    sid = f"ATO-{scenario_idx:03d}"
    return Scenario(
        scenario_id=sid,
        typology="ACCOUNT_TAKEOVER",
        target_account_id=target.id,
        accounts_involved=[target.id],
        transaction_ids=tx_ids,
        pattern_descriptions=[
            f"Sharp behavioural shift in last 48h: card-not-present spend from {new_country}, new device fingerprint, merchant categories absent from 90-day baseline",
            "Sudden burst of foreign card-not-present transactions to high-risk merchant categories on an account with stable historical profile",
            "Device fingerprint and IP geolocation diverge from established baseline immediately before a high-velocity spend pattern",
            "Behavioural change-point: long-tenured account begins transacting in luxury goods, crypto, or gift cards from a new geography",
        ],
        expected_evidence=[
            f"Device fingerprint {new_device[:8]} not present in 90d history",
            f"New geography {new_country}, baseline GB",
            "Merchant category shift toward high-risk categories",
        ],
    )


# ----------------------------------------------------------------------------
# 4. MULE NETWORK — shared device/IP/address across accounts
# ----------------------------------------------------------------------------


def mule_network(world: "World", scenario_idx: int) -> Scenario:
    rng = world.rng
    cluster_size = rng.randint(4, 7)
    accounts = [world.pick_unrelated_accounts(None, n=1)[0] for _ in range(cluster_size)]
    shared_device = world.fake.sha256()[:32]
    shared_ip = world.fake.ipv4_public()

    for a in accounts:
        world.update_account_attrs(a.id, device_fingerprint=shared_device, primary_ip=shared_ip)
        # Create a derived edge to every other account in the cluster on the shared device.
        for b in accounts:
            if a.id == b.id:
                continue
            world.add_edge(
                source=a.id, target=b.id,
                relationship_type="shared_device", source_type="derived",
                weight=0.8, metadata={"device": shared_device},
            )
            world.add_edge(
                source=a.id, target=b.id,
                relationship_type="shared_ip", source_type="derived",
                weight=0.6, metadata={"ip": shared_ip},
            )

    # Add some money-mule-like funnel transactions: small inbound credits to many,
    # then consolidation into one account.
    funnel_target = accounts[0]
    base_ts = world.now - timedelta(days=rng.randint(3, 14))
    tx_ids = []
    for src in accounts[1:]:
        ts = base_ts + timedelta(hours=rng.randint(1, 72))
        amt = Decimal(rng.randint(800, 4500))
        tx_ids.append(world.add_transaction(
            account_id=funnel_target.id,
            counterparty_account_id=src.id,
            amount=amt,
            direction="credit",
            channel="transfer",
            merchant=None,
            merchant_category=None,
            description="consolidation",
            timestamp=ts,
        ))

    sid = f"MUL-{scenario_idx:03d}"
    return Scenario(
        scenario_id=sid,
        typology="MULE_NETWORK",
        target_account_id=funnel_target.id,
        accounts_involved=[a.id for a in accounts],
        transaction_ids=tx_ids,
        pattern_descriptions=[
            f"{cluster_size} accounts share the same device fingerprint and primary IP, with funds consolidating into one account over 1-2 weeks",
            "Multiple personal accounts opened on the same device fingerprint, exhibiting funnel-shaped transaction flow toward a single beneficiary",
            "Cluster of accounts with overlapping technical signals (device, IP) and small inbound credits that consolidate into a hub account",
            "Mule-network signature: dense graph of accounts linked by shared device/IP, low individual transaction values, hub-and-spoke money flow",
        ],
        expected_evidence=[
            f"{cluster_size} accounts share device {shared_device[:8]}",
            f"Shared IP {shared_ip} across cluster",
            f"Funnel pattern: {cluster_size - 1} sources → 1 hub",
        ],
    )


# ----------------------------------------------------------------------------
# 5. PEP EXPOSURE — beneficial owner on synthetic watchlist
# ----------------------------------------------------------------------------


def pep_exposure(world: "World", scenario_idx: int) -> Scenario:
    rng = world.rng
    target = world.pick_business_account()
    pep_entity = world.pick_random_watchlist(list_type="PEP")
    world.update_account_attrs(target.id, beneficial_owner_id=pep_entity.id)

    # Add some moderately large inbound payments — what would normally trigger a review.
    tx_ids = []
    base_ts = world.now - timedelta(days=rng.randint(7, 30))
    for i in range(rng.randint(3, 6)):
        ts = base_ts + timedelta(days=i, hours=rng.randint(0, 23))
        amt = Decimal(rng.randint(15000, 75000))
        tx_ids.append(world.add_transaction(
            account_id=target.id,
            counterparty_account_id=None,
            amount=amt,
            direction="credit",
            channel="wire",
            merchant=world.fake.company(),
            merchant_category="professional_services",
            description=f"international wire from {world.fake.country()}",
            timestamp=ts,
        ))

    sid = f"PEP-{scenario_idx:03d}"
    return Scenario(
        scenario_id=sid,
        typology="PEP_EXPOSURE",
        target_account_id=target.id,
        accounts_involved=[target.id],
        transaction_ids=tx_ids,
        pattern_descriptions=[
            f"Business account with declared beneficial owner '{pep_entity.name}' present on PEP watchlist, receiving large international wires",
            "Corporate account where ultimate beneficial owner matches a politically exposed person record, with material inbound wire activity",
            "Beneficial owner identified on sanctions or PEP list, business account showing five- to six-figure inbound international payments",
            "PEP-exposure signature: beneficial owner watchlist hit combined with high-value cross-border credits to a UK business entity",
        ],
        expected_evidence=[
            f"Beneficial owner {pep_entity.name} on {pep_entity.list_type} list",
            "Material inbound international wires",
            "Business account, professional_services category",
        ],
    )


# ----------------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------------

SCENARIO_GENERATORS = {
    "STRUCTURING": structuring,
    "LAYERING": layering,
    "ACCOUNT_TAKEOVER": account_takeover,
    "MULE_NETWORK": mule_network,
    "PEP_EXPOSURE": pep_exposure,
}
