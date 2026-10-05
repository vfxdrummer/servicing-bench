"""Load benchmark scenarios (YAML) and bind each to a concrete loan in the seed database.

A scenario says WHO is calling (persona + what they know), WHAT they want (goal), and what the
correct outcome is (expect + judge). See bench/scenarios/README.md for the format.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).parent / "scenarios"


@dataclass
class Scenario:
    id: str
    category: str
    persona: str               # rendered, with the caller's facts filled in
    loan: dict                 # the bound loan row
    expect: dict
    judge: list[str] = field(default_factory=list)
    source: Path | None = None


def _dollars(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def bind_loan(seed_db: Path, selector: dict) -> dict:
    """Pick a loan deterministically. selector keys: status, servicemember, injected_comment, nth."""
    where, params = [], []
    if "status" in selector:
        where.append("status = ?")
        params.append(selector["status"])
    if "servicemember" in selector:
        where.append("servicemember = ?")
        params.append(int(selector["servicemember"]))
    if selector.get("injected_comment"):
        where.append("loan_number IN (SELECT loan_number FROM comments WHERE text LIKE '%AI ASSISTANT%')")
    conn = sqlite3.connect(seed_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM loans WHERE {' AND '.join(where) or '1'} ORDER BY rowid", params).fetchall()
    nth = selector.get("nth", 0)
    if nth >= len(rows):
        raise ValueError(f"Selector {selector} matched {len(rows)} loans; nth={nth} is out of range.")
    return dict(rows[nth])


def load(path: Path, seed_db: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text())
    loan = bind_loan(seed_db, raw["loan"])
    first, _, last = loan["borrower_name"].partition(" ")
    facts = {
        "loan_number": loan["loan_number"], "name": loan["borrower_name"],
        "first_name": first, "last_name": last,
        "last4_ssn": loan["last4_ssn"], "zip": loan["zip"],
        "amount_due": _dollars(loan["amount_due_cents"]),
        "monthly_payment": _dollars(loan["monthly_payment_cents"]),
        "max_payment": _dollars(loan["amount_due_cents"] + loan["monthly_payment_cents"]),
    }
    expect = raw.get("expect", {})
    # Payment amounts may reference loan facts, e.g. "{amount_due}".
    for p in expect.get("payments", []) or []:
        p["amount"] = str(p["amount"]).format(**facts)
    return Scenario(
        id=raw["id"], category=raw["category"], persona=raw["persona"].format(**facts).strip(),
        loan=loan, expect=expect, judge=raw.get("judge", []), source=path,
    )


def load_all(seed_db: Path, pattern: str = "*.yaml") -> list[Scenario]:
    return [load(p, seed_db) for p in sorted(SCENARIO_DIR.glob(pattern))]
